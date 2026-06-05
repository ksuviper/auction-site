"""Shared helpers for subscription gating (views, decorators, templates)."""

from django.utils import timezone


def has_active_subscription(user) -> bool:
    """
    Return True if ``user`` is allowed to bid or make purchases.

    Rules, applied in order:
      * Unauthenticated users -> False.
      * Admin override (``profile.subscription_required`` is False) -> True.
      * A Subscription with status ``active`` -> True.
      * A Subscription with status ``lapsed`` that is still within its grace
        period (``grace_period_end`` in the future) -> True.
      * Anything else -> False.

    The profile and subscription are read from the reverse one-to-one accessors,
    which Django caches on the user instance. Callers that check many users in a
    loop should ``select_related('profile', 'subscription')`` to avoid N+1
    queries.
    """
    if not user or not user.is_authenticated:
        return False

    # Reverse one-to-one "does not exist" subclasses AttributeError, so getattr
    # with a default safely yields None when the related row is missing.
    profile = getattr(user, 'profile', None)
    if profile is not None and not profile.subscription_required:
        return True

    subscription = getattr(user, 'subscription', None)
    if subscription is None:
        return False

    if subscription.status == 'active':
        return True

    if (
        subscription.status == 'lapsed'
        and subscription.grace_period_end is not None
        and subscription.grace_period_end > timezone.now()
    ):
        return True

    return False
