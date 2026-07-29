"""View decorators for subscription gating.

Provided for function-based views (used by later prompts). Class-based views
gate inline via ``has_active_subscription`` instead.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from .utils import has_active_subscription


def subscription_required(view_func):
    """
    Require an active (or admin-exempt) membership to access ``view_func``.

    Unauthenticated users are sent to the login page (preserving ``next``);
    authenticated users without an active membership are redirected to the
    subscribe page with a warning message.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not has_active_subscription(request.user):
            messages.warning(
                request,
                'A membership is required to place bids or make purchases. '
                'Choose a plan below.',
            )
            return redirect('subscribe')
        return view_func(request, *args, **kwargs)

    return _wrapped
