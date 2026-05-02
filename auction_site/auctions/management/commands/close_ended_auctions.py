"""
Management command: close_ended_auctions

Finds every AuctionListing whose auction window has passed, marks it closed,
assigns the winner, creates an Invoice, and dispatches email notifications.

Run manually:
    python manage.py close_ended_auctions

Normally scheduled every 5 minutes by APScheduler (see auctions/scheduler.py).
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from auctions.models import AuctionListing, Invoice

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Close ended auctions, assign winners, generate invoices, and notify participants.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be closed without making any changes.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        ended = (
            AuctionListing.objects
            .filter(ends_at__lte=now, is_closed=False)
            .select_related('seller', 'category', 'winner')
        )

        count = ended.count()
        if count == 0:
            self.stdout.write('No auctions to close.')
            return

        if dry_run:
            self.stdout.write(f'[DRY RUN] Would close {count} listing(s):')
            for listing in ended:
                top_bid = listing.bids.order_by('-amount').first()
                winner = top_bid.bidder.username if top_bid else 'no bids'
                self.stdout.write(f'  • [{listing.pk}] {listing.title} — winner: {winner}')
            return

        closed = invoiced = errors = 0

        for listing in ended:
            try:
                winner, invoice = self._close_listing(listing)
                closed += 1
                if invoice:
                    invoiced += 1
                # Email sending is outside the transaction so a failed email
                # does not roll back the close/invoice.
                self._send_notifications(listing, winner, invoice)
            except Exception:
                errors += 1
                logger.exception('Error closing listing pk=%d "%s"', listing.pk, listing.title)
                self.stderr.write(
                    self.style.ERROR(f'Error closing listing {listing.pk}: {listing.title}')
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done: closed={closed}, invoiced={invoiced}, errors={errors}'
            )
        )

    # ── Core close logic ─────────────────────────────────────────────────────

    @transaction.atomic
    def _close_listing(self, listing):
        """
        Close the listing atomically.  Returns (top_bid_or_None, invoice_or_None).
        """
        top_bid = listing.bids.select_related('bidder').order_by('-amount').first()

        listing.is_closed = True
        listing.is_active = False
        listing.winner = top_bid.bidder if top_bid else None
        listing.save(update_fields=['is_closed', 'is_active', 'winner', 'updated_at'])

        invoice = None
        if top_bid and listing.seller:
            invoice = Invoice.objects.create(
                listing=listing,
                buyer=top_bid.bidder,
                seller=listing.seller,
                amount=top_bid.amount,
                shipping_fee=listing.seller.shipping_fee,
                payment_method='',      # winner confirms their preferred method
                notes='',
                is_sent=False,
                is_manually_created=False,
            )
            logger.info(
                'Invoice #%d created for listing "%s" (winner: %s, amount: $%s)',
                invoice.pk, listing.title, top_bid.bidder.username, top_bid.amount,
            )
        elif top_bid and not listing.seller:
            logger.warning(
                'Listing "%s" has no seller — skipping invoice creation.', listing.title
            )

        return top_bid, invoice

    # ── Email notifications ───────────────────────────────────────────────────

    def _send_notifications(self, listing, top_bid, invoice):
        if top_bid:
            self._email_winner(listing, top_bid, invoice)
            self._email_seller(listing, top_bid)
            self._email_admin(listing, top_bid, invoice)
        else:
            self._email_admin_no_bids(listing)

    def _email_winner(self, listing, top_bid, invoice):
        buyer = top_bid.bidder
        if not buyer.email:
            logger.warning(
                'Winner %s has no email address — skipping winner notification.', buyer.username
            )
            return

        seller = listing.seller
        seller_block = ''
        if seller:
            seller_block = (
                f'\nSeller:              {seller.name}'
                f'\nAccepted payments:   {seller.accepted_payment_methods}'
                f'\nShipping fee:        ${seller.shipping_fee}'
            )

        body = f"""\
Congratulations, {buyer.username}!

You won the auction for:
  {listing.title}

Winning bid:  ${top_bid.amount}
{seller_block}

The seller will reach out with payment instructions. Please have your
shipping address ready and confirm your preferred payment method.

Thank you for bidding with ASQ Daylily Auctions!

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject=f'You won: {listing.title}',
            body=body,
            recipients=[buyer.email],
        )

    def _email_seller(self, listing, top_bid):
        """
        Seller notification sent directly to seller.email when set; otherwise
        falls back to ADMIN_EMAIL for manual forwarding.
        """
        seller = listing.seller
        seller_email = (seller.email if seller and seller.email else '')
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')
        recipient = seller_email or admin_email
        if not recipient:
            return

        buyer = top_bid.bidder
        seller_name = seller.name if seller else '(no seller on record)'
        routing_note = '' if seller_email else '\n[Note: seller has no email — forwarded to admin]\n'

        body = f"""\
Hello,{routing_note}
The following auction has ended and needs your attention:

Listing:        {listing.title}
Seller:         {seller_name}
Winner:         {buyer.username}
Winner email:   {buyer.email or '(not provided)'}
Winning bid:    ${top_bid.amount}
Ended at:       {listing.ends_at.strftime('%Y-%m-%d %H:%M %Z')}

Please contact the buyer to arrange payment and shipping.

-- ASQ Daylily Auction System
"""
        _safe_send(
            subject=f'Your auction ended: {listing.title}',
            body=body,
            recipients=[recipient],
        )

    def _email_admin(self, listing, top_bid, invoice):
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')
        if not admin_email:
            return

        buyer = top_bid.bidder
        invoice_line = f'Invoice #{invoice.pk}' if invoice else 'None (no seller on listing)'

        body = f"""\
[ASQ] Auction Closed — Summary
===============================
Listing ID:   {listing.pk}
Title:        {listing.title}
Category:     {listing.category.name}
Seller:       {listing.seller.name if listing.seller else 'None'}

Winner:       {buyer.username}
Email:        {buyer.email or '(none)'}
Winning bid:  ${top_bid.amount}
Invoice:      {invoice_line}

Closed at:    {timezone.now().strftime('%Y-%m-%d %H:%M %Z')}

-- ASQ Daylily Auction System
"""
        _safe_send(
            subject=f'[ASQ Admin] Closed: {listing.title}',
            body=body,
            recipients=[admin_email],
        )

    def _email_admin_no_bids(self, listing):
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')
        if not admin_email:
            return

        body = f"""\
[ASQ] Auction Ended — No Bids
==============================
Listing ID:     {listing.pk}
Title:          {listing.title}
Category:       {listing.category.name}
Seller:         {listing.seller.name if listing.seller else 'None'}
Starting price: ${listing.start_price}

No bids were placed. No invoice was created.
Ended at:       {listing.ends_at.strftime('%Y-%m-%d %H:%M %Z')}

-- ASQ Daylily Auction System
"""
        _safe_send(
            subject=f'[ASQ Admin] No bids: {listing.title}',
            body=body,
            recipients=[admin_email],
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_send(subject, body, recipients):
    """send_mail wrapper that logs failures without raising."""
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        logger.debug('Email sent: "%s" → %s', subject, recipients)
    except Exception:
        logger.exception('Failed to send email "%s" to %s', subject, recipients)
