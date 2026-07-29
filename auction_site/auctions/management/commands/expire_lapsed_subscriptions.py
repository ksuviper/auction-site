"""
Management command: expire_lapsed_subscriptions

Cancels memberships that lapsed (payment failed) and whose grace period has now
passed. Affected users are emailed and pointed back to the subscribe page.

Run manually:
    python manage.py expire_lapsed_subscriptions
    python manage.py expire_lapsed_subscriptions --dry-run

Normally scheduled daily at 02:00 by APScheduler (see auctions/scheduler.py).
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from auctions.models import Subscription
from auctions.utils import _safe_send

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Cancel lapsed memberships whose grace period has expired and notify users.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be cancelled without making any changes.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        expired = (
            Subscription.objects
            .filter(status='lapsed', grace_period_end__isnull=False, grace_period_end__lt=now)
            .select_related('user')
        )

        count = expired.count()
        if count == 0:
            self.stdout.write('No lapsed memberships past their grace period.')
            return

        if dry_run:
            self.stdout.write(f'[DRY RUN] Would cancel {count} membership(s):')
            for sub in expired:
                self.stdout.write(
                    f'  • {sub.user.username} (grace ended {sub.grace_period_end:%Y-%m-%d})'
                )
            return

        cancelled = errors = 0
        for sub in expired:
            try:
                sub.status = 'cancelled'
                sub.cancelled_at = now
                sub.save(update_fields=['status', 'cancelled_at'])
                self._email_membership_ended(sub)
                cancelled += 1
            except Exception:
                errors += 1
                logger.exception('Error expiring subscription for %s', sub.user.username)

        self.stdout.write(
            self.style.SUCCESS(f'Done: cancelled={cancelled}, errors={errors}')
        )

    def _email_membership_ended(self, sub):
        user = sub.user
        if not user.email:
            return
        body = """\
Your ASQ Daylily Auctions membership has ended.

Your payment could not be processed and the grace period has now passed, so your
membership has been cancelled and your bidding access has been paused.

You can re-subscribe any time to restore full access:
    /subscribe/

Thank you for being part of the ASQ Daylily Auction Group.

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject='Your ASQ Daylily Auctions membership has ended',
            body=body,
            recipients=[user.email],
        )
