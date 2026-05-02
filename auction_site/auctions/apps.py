import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _start_scheduler():
    """
    Start APScheduler exactly once.

    Startup rules:
    - Never start during tests.
    - Only start when the process is actually serving HTTP requests:
        • `manage.py runserver` (dev) — but only in the worker process, not
          the reloader parent.  Django sets RUN_MAIN='true' in the worker.
        • WSGI / ASGI server (gunicorn, uvicorn, etc.) — sys.argv[0] will NOT
          end with 'manage.py', so all plain management commands are skipped.
    """
    if 'test' in sys.argv:
        return

    # Determine whether we are being invoked as an HTTP server.
    # manage.py <anything-other-than-runserver> → skip.
    is_manage = sys.argv and sys.argv[0].endswith('manage.py')
    if is_manage and 'runserver' not in sys.argv:
        return

    # In development, skip the reloader parent process.
    if 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true':
        return

    try:
        from auctions.scheduler import start
        start()
    except Exception:
        logger.exception('APScheduler could not be started.')


class AuctionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'auctions'
    verbose_name = 'Auction Management'

    def ready(self):
        import auctions.signals  # noqa: F401
        _start_scheduler()
