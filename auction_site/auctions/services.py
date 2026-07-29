"""Auction domain services."""

import logging
from decimal import Decimal

from django.db import transaction

from .models import AuctionListing, Bid, ProxyBid
from .utils import _safe_send

logger = logging.getLogger(__name__)

INCREMENT = Decimal('1.00')


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
