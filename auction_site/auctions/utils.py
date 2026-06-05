"""Shared helpers for subscription gating (views, decorators, templates)."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def _safe_send(subject, body, recipients):
    """
    send_mail wrapper that logs failures without raising.

    Mirrors the helper in close_ended_auctions.py; lives here so the
    subscription, buy-now, and comment flows can reuse it. (The duplicate copy
    in close_ended_auctions.py is consolidated onto this one in a later step.)
    """
    if not recipients:
        return
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        logger.debug('Email sent: "%s" -> %s', subject, recipients)
    except Exception:
        logger.exception('Failed to send email "%s" to %s', subject, recipients)


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
