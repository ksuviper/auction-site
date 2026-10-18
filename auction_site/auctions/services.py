"""Auction domain services."""

import logging
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AuctionListing, Bid, ProxyBid
from .utils import _safe_send, send_push_to_user

logger = logging.getLogger(__name__)

INCREMENT = Decimal('1.00')

# What a copy inherits: everything describing the plant and how it is sold.
# Anything about a particular run of the listing — dates, stock left, bids,
# winner, open/closed — is reset by copy_listing() instead.
#
# One list, used by both the admin Duplicate action and Copy Existing Listing on
# the Add Listing page, so the two cannot drift into copying different things. A
# new descriptive field belongs here the day it is added to the model.
COPIED_LISTING_FIELDS = (
    'title',
    'description',
    'image',
    'category_id',
    'seller_id',
    'listing_type',
    'start_price',
    'reserve_price',
    'buy_now_price',
    'quantity_available',
    'additional_item_discount',
    'shipping_mode',
    'shipping_fee',
)


def generate_combined_invoices():
    """
    Gather every unbilled sale into draft invoices, one per buyer+seller pair.

    Returns the drafts that were created or added to, newest first.

    "Unbilled" means ``Invoice.combined_invoice is None`` — there is no batch
    boundary and no time window. Everything a buyer owes a seller that has not
    been sent to them yet goes onto one invoice, however long ago it closed or
    how many separate closing events it came from.

    A pair with an existing *draft* has its new items added to that draft
    rather than getting a second one; only once a draft has been sent does the
    next win start a fresh tab. The database enforces that too — see the
    UniqueConstraint on CombinedInvoice.
    """
    from .models import CombinedInvoice, Invoice

    with transaction.atomic():
        unbilled = list(
            Invoice.objects
            .filter(combined_invoice__isnull=True)
            .values_list('pk', 'buyer_id', 'seller_id')
        )
        if not unbilled:
            return []

        by_pair = {}
        for invoice_pk, buyer_id, seller_id in unbilled:
            by_pair.setdefault((buyer_id, seller_id), []).append(invoice_pk)

        drafts = []
        for (buyer_id, seller_id), invoice_pks in by_pair.items():
            draft, created = CombinedInvoice.objects.get_or_create(
                buyer_id=buyer_id, seller_id=seller_id, status='draft'
            )
            Invoice.objects.filter(pk__in=invoice_pks).update(
                combined_invoice=draft
            )
            drafts.append(draft)
            logger.info(
                'Combined invoice #%d %s for buyer=%s seller=%s (+%d item(s))',
                draft.pk, 'created' if created else 'updated',
                buyer_id, seller_id, len(invoice_pks),
            )

    drafts.sort(key=lambda d: d.pk, reverse=True)
    return drafts


def send_combined_invoice(combined_invoice, request=None):
    """
    Email the buyer their itemised invoice and mark it sent.

    This is the only point at which a buyer hears that they won or bought
    anything, so the email carries the full breakdown and the seller's payment
    details — nothing earlier told them either.

    Returns True if it was sent. An already-sent invoice returns False rather
    than emailing twice; the caller decides what to tell the admin.

    The status is committed before the email goes out. If sending fails,
    _safe_send logs it and the invoice still reads as sent — which is the safer
    of the two wrong answers, since the alternative is a retry that bills the
    buyer twice for the same plants.
    """
    from .models import Invoice
    from .utils import _safe_send, payment_block, seller_display_name

    if combined_invoice.status != 'draft':
        return False

    line_items = list(
        combined_invoice.line_items.select_related('listing').order_by('pk')
    )

    with transaction.atomic():
        combined_invoice.status = 'sent'
        combined_invoice.sent_at = timezone.now()
        combined_invoice.save(update_fields=['status', 'sent_at'])
        # Keep the per-invoice flag in step, so the older invoice dashboard and
        # the reports do not show these as still outstanding.
        Invoice.objects.filter(combined_invoice=combined_invoice).update(
            is_sent=True
        )

    buyer = combined_invoice.buyer
    if not buyer.email:
        logger.warning(
            'Combined invoice #%d has no buyer email; marked sent without '
            'emailing.', combined_invoice.pk,
        )
        return True

    seller = combined_invoice.seller
    lines = []
    for item in line_items:
        quantity = f' x{item.quantity}' if item.quantity > 1 else ''
        lines.append(
            f'  {item.item_display}{quantity}\n'
            f'      Item: ${item.amount}   Shipping: ${item.shipping_fee}'
        )
    items_block = '\n'.join(lines) or '  (no items)'

    shipping_note = ''
    if combined_invoice.shipping_override is not None:
        shipping_note = ' (combined shipping)'

    discount_line = ''
    if combined_invoice.discount_amount:
        discount_line = f'\nDiscount:       -${combined_invoice.discount_amount}'

    notes_block = ''
    if combined_invoice.notes.strip():
        notes_block = f'\nA note from us:\n{combined_invoice.notes.strip()}\n'

    body = f"""\
Hello {buyer.get_full_name() or buyer.username},

Here is your invoice for plants from {seller_display_name(seller)}.

{items_block}

Subtotal:       ${combined_invoice.subtotal}
Shipping:       ${combined_invoice.total_shipping}{shipping_note}{discount_line}
TOTAL DUE:      ${combined_invoice.total}
{notes_block}
Please pay {seller_display_name(seller)} directly using one of these:

                 {payment_block(getattr(seller, 'profile', None))}

Thank you for supporting our growers!

-- ASQ Daylily Auction Group
"""
    _safe_send(
        subject=(
            f'Your ASQ Daylily invoice from {seller_display_name(seller)}'
        ),
        body=body,
        recipients=[buyer.email],
    )
    logger.info(
        'Combined invoice #%d sent to %s (total $%s)',
        combined_invoice.pk, buyer.email, combined_invoice.total,
    )
    return True


def copy_listing(source):
    """
    Build an unsaved copy of ``source``, ready for new dates.

    Returns a new AuctionListing that is *not* saved: starts_at and ends_at are
    None, and the model requires both, so the caller decides when it goes live.
    Stock is restocked to full rather than inherited — a copy is next week's
    listing, not a continuation of how far the original sold down — and bidding
    history, the winner and the closed/active flags all start clean.

    The image is shared with the source rather than duplicated in storage: both
    rows point at the same uploaded file, so deleting one listing does not take
    the other's photo with it (Django does not delete files on row delete).
    """
    copy = AuctionListing(
        **{field: getattr(source, field) for field in COPIED_LISTING_FIELDS}
    )

    copy.starts_at = None
    copy.ends_at = None
    copy.current_bid = 0
    copy.winner = None
    copy.is_closed = False
    # Inactive until an admin has given it dates and looked it over; an active
    # listing with no dates is not something to create by accident.
    copy.is_active = False
    if copy.listing_type == 'buy_now':
        copy.quantity_remaining = copy.quantity_available

    return copy


@transaction.atomic
def run_proxy_bids(listing):
    """
    Resolve automatic (proxy) bids for ``listing``.

    Places a single auto-bid for the highest-max proxy bidder so they lead at
    the minimum amount needed ($1 over the next-highest max, or over the current
    bid / starting price), capped at their own maximum. Proxy bidders who can no
    longer win are deactivated and emailed. Returns the winning proxy bidder
    (the User a bid was placed for), or None if nothing was placed.
    """
    listing = AuctionListing.objects.select_for_update().get(pk=listing.pk)

    proxy_bids = list(
        ProxyBid.objects.filter(listing=listing, is_active=True)
        .select_related('bidder')
        .order_by('-max_amount', 'created_at')
    )
    if not proxy_bids:
        return None

    top_proxy = proxy_bids[0]
    second_proxy = proxy_bids[1] if len(proxy_bids) > 1 else None

    top_manual = listing.bids.select_related('bidder').order_by('-amount').first()
    current_high_bidder = top_manual.bidder if top_manual else None
    current_bid = listing.current_bid

    already_leads = (
        current_high_bidder is not None and current_high_bidder == top_proxy.bidder
    )

    # The top proxy is already winning and there is no competing proxy that
    # could push the price up — leave it alone (don't bid against yourself).
    if already_leads and second_proxy is None:
        return None

    if second_proxy is not None:
        target = second_proxy.max_amount + INCREMENT
    else:
        target = max(current_bid, listing.start_price) + INCREMENT
    auto_amount = min(target, top_proxy.max_amount)

    placed_bidder = None
    if auto_amount > current_bid and auto_amount >= listing.start_price:
        Bid.objects.create(listing=listing, bidder=top_proxy.bidder, amount=auto_amount)
        listing.current_bid = auto_amount
        listing.save(update_fields=['current_bid', 'updated_at'])
        current_bid = auto_amount
        placed_bidder = top_proxy.bidder

    # Notify + deactivate any active proxy bidder who can no longer win.
    leader_id = (
        placed_bidder.id if placed_bidder
        else (current_high_bidder.id if current_high_bidder else None)
    )
    for pb in proxy_bids:
        if pb.bidder_id == leader_id:
            continue
        if pb.max_amount <= current_bid and pb.is_active:
            pb.is_active = False
            pb.save(update_fields=['is_active'])
            _notify_outbid(pb, listing, current_bid)

    return placed_bidder


def _notify_outbid(proxy_bid, listing, current_bid):
    user = proxy_bid.bidder
    if not user.email:
        # No address to email, but a device may still be listening.
        _push_outbid(user, listing)
        return
    body = f"""\
You have been outbid on "{listing.title}".

The current bid is now ${current_bid}, which is above your maximum bid of
${proxy_bid.max_amount}. Your automatic bidding for this item has stopped.

If you'd still like to win, visit the listing and set a new, higher maximum bid.

-- ASQ Daylily Auction Group
"""
    _safe_send(
        subject=f'You were outbid: {listing.title}',
        body=body,
        recipients=[user.email],
    )
    _push_outbid(user, listing)


def _push_outbid(user, listing):
    """
    The push half of the outbid notice. Additive: the email above has already
    been sent by the time this is reached, whatever happens here.

    Deferred until the bid commits. run_proxy_bids holds a row lock on the
    listing, and a push is a network round trip to Google or Apple per device —
    nobody else should be kept waiting to bid on this plant while that happens.
    It also means a bid that rolls back never sends an alert about itself.
    """
    url = reverse('listing_detail', args=[listing.pk])
    title = listing.title

    def send():
        send_push_to_user(
            user,
            "You've been outbid",
            f'Someone bid higher on {title}.',
            url=url,
            tag=f'outbid-{listing.pk}',
        )

    transaction.on_commit(send)


# How many plant names the "new listings" email spells out before saying "and
# N more". A list of forty titles reads as a catalogue rather than a heads-up.
NEW_LISTINGS_EMAIL_TITLES = 10


def notify_followers_of_new_listings(seller, request=None):
    """
    Tell everyone following ``seller`` that their new listings are up.

    Manual and per seller, never automatic per listing: an admin presses it
    once when a seller's week of plants is ready, rather than every follower
    getting an alert for every plant as it is typed in.

    Every follower gets the email — that is the reliable path and it does not
    depend on push. Each is then also sent a push, which quietly does nothing
    for anyone who has not turned push on. The email always goes first, so a
    push problem cannot stop it.

    Returns a dict of counts for the admin's confirmation message:
    ``followers``, ``emailed`` (followers with an address), ``pushed`` (devices
    that accepted a push).
    """
    from allauth.utils import build_absolute_uri

    from .models import SellerFollow
    from .utils import seller_display_name

    seller_name = seller_display_name(seller)
    path = reverse('seller_listings', args=[seller.pk])
    link = (
        request.build_absolute_uri(path) if request is not None
        else build_absolute_uri(None, path)
    )

    profile = getattr(seller, 'profile', None)
    titles = (
        list(profile.active_listings().values_list('title', flat=True))
        if profile is not None else []
    )
    if titles:
        shown = titles[:NEW_LISTINGS_EMAIL_TITLES]
        listed = '\n'.join(f'  • {title}' for title in shown)
        if len(titles) > len(shown):
            listed += f'\n  …and {len(titles) - len(shown)} more'
        listing_block = f'Now listed:\n\n{listed}\n'
    else:
        listing_block = ''

    followers = (
        SellerFollow.objects
        # A deactivated account has been switched off by an admin; it should
        # not keep receiving club mail just because it once followed someone.
        .filter(seller=seller, user__is_active=True)
        .select_related('user')
        .order_by('pk')
    )

    counts = {'followers': 0, 'emailed': 0, 'pushed': 0}
    for follow in followers:
        follower = follow.user
        counts['followers'] += 1

        if follower.email:
            body = f"""\
Hello {follower.get_full_name() or follower.username},

{seller_name} has new plants up for auction.

{listing_block}
See everything {seller_name} has listed:
{link}

You're receiving this because you follow {seller_name}. To stop, open their
page and choose "Unfollow", or manage the sellers you follow from your
notification settings.

-- ASQ Daylily Auction Group
"""
            _safe_send(
                subject=f'New listings from {seller_name}',
                body=body,
                recipients=[follower.email],
            )
            counts['emailed'] += 1

        counts['pushed'] += send_push_to_user(
            follower,
            f'New listings from {seller_name}',
            'Their latest plants are up for auction.',
            url=path,
            tag=f'new-listings-{seller.pk}',
        )

    if profile is not None:
        type(profile).objects.filter(pk=profile.pk).update(
            followers_notified_at=timezone.now()
        )

    logger.info(
        'Followers of seller %s notified: %s', seller.pk, counts,
    )
    return counts
