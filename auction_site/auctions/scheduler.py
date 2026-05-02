"""
APScheduler configuration for ASQ Daylily Auctions.

The scheduler runs background jobs inside the Django process.
In production, prefer a dedicated cron job calling the management command:
    */5 * * * * /path/to/venv/bin/python manage.py close_ended_auctions

The scheduler is started from AuctionsConfig.ready() and guards against
double-starts in Django's development auto-reloader.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_scheduler = None


def _run_close_ended_auctions():
    """Job target — delegates to the management command for a single code path."""
    from django.core.management import call_command
    call_command('close_ended_auctions', verbosity=0)


def _run_deactivate_old_listings():
    from django.core.management import call_command
    call_command('deactivate_old_listings', verbosity=0)


def start():
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.debug('APScheduler already running — skipping start.')
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
        from django_apscheduler.jobstores import DjangoJobStore

        _scheduler = BackgroundScheduler(timezone=getattr(settings, 'TIME_ZONE', 'UTC'))
        _scheduler.add_jobstore(DjangoJobStore(), 'default')

        _scheduler.add_job(
            _run_close_ended_auctions,
            trigger=IntervalTrigger(minutes=5),
            id='close_ended_auctions',
            name='Close ended auctions and generate invoices',
            max_instances=1,
            replace_existing=True,
        )

        # Run every Monday at 03:00 local time to clean up listings from last week.
        _scheduler.add_job(
            _run_deactivate_old_listings,
            trigger=CronTrigger(day_of_week='mon', hour=3, minute=0),
            id='deactivate_old_listings',
            name='Deactivate listings that ended more than 7 days ago',
            max_instances=1,
            replace_existing=True,
        )

        _scheduler.start()
        logger.info(
            'APScheduler started — "close_ended_auctions" every 5 min, '
            '"deactivate_old_listings" every Monday 03:00.'
        )
    except Exception:
        logger.exception('Failed to start APScheduler.')
