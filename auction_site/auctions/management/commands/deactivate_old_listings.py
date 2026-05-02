"""
Management command: deactivate_old_listings

Sets is_active=False on every AuctionListing whose ends_at is more than
7 days in the past.  Safe to run repeatedly — already-inactive listings
are not touched.

Run manually:
    python manage.py deactivate_old_listings

Scheduled weekly by APScheduler (see auctions/scheduler.py).
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from auctions.models import AuctionListing

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Deactivate listings that ended more than 7 days ago.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview which listings would be deactivated without changing anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=7)

        qs = AuctionListing.objects.filter(ends_at__lt=cutoff, is_active=True)
        count = qs.count()

        if count == 0:
            self.stdout.write('No listings to deactivate.')
            return

        if dry_run:
            self.stdout.write(f'[DRY RUN] Would deactivate {count} listing(s):')
            for listing in qs:
                self.stdout.write(f'  • [{listing.pk}] {listing.title} — ended {listing.ends_at:%Y-%m-%d}')
            return

        updated = qs.update(is_active=False)
        logger.info('Deactivated %d old listing(s) (ended before %s).', updated, cutoff.date())
        self.stdout.write(self.style.SUCCESS(f'Deactivated {updated} listing(s).'))
