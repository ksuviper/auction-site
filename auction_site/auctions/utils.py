"""Shared helpers for subscription gating (views, decorators, templates)."""

import logging
import os

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
TURNSTILE_VERIFY_TIMEOUT = 5  # seconds; a signup POST should not hang on this


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


def seller_display_name(user) -> str:
    """
    How to refer to a seller in emails, CSV exports and admin columns.

    Delegates to UserProfile.display_name so screens and emails cannot drift
    apart, and falls back to the username if the profile row is somehow missing
    — a name is never important enough to raise on.
    """
    if user is None:
        return ''
    profile = getattr(user, 'profile', None)
    if profile is not None:
        return profile.display_name
    return user.get_username()


def payment_block(profile, indent='                 ') -> str:
    """
    A seller's payment details laid out for a plain-text email.

    Reads UserProfile.payment_options so an email and the invoice page cannot
    end up telling a buyer to pay two different Venmo handles. Multiple methods
    go one per line, aligned under the first, which a single comma-joined run
    of handles is genuinely hard to read.
    """
    options = getattr(profile, 'payment_options', None) or []
    if not options:
        return '(ask the seller)'
    lines = [f'{label}: {detail}' for label, detail in options]
    return f'\n{indent}'.join(lines)


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


def needs_admin_approval(user) -> bool:
    """
    Return True if ``user`` may not log in yet because an admin has not
    approved their account.

    Staff and superusers are exempt: accounts made with ``createsuperuser`` (or
    promoted in the admin) have no one above them to grant approval, and locking
    them out would leave nobody able to approve anyone else.

    A user with no profile row counts as unapproved. Profiles are created by a
    post_save signal and the 0013 migration approved every profile that predates
    the gate, so a missing profile means something unexpected happened — holding
    the login is the safe reading.
    """
    if user is None:
        return False
    if user.is_staff or user.is_superuser:
        return False

    profile = getattr(user, 'profile', None)
    if profile is None:
        logger.warning(
            'User %s has no profile row; treating as awaiting approval.', user.pk
        )
        return True

    return not profile.is_approved


def client_ip(request) -> str:
    """
    Best-effort client IP for the current request.

    Reads REMOTE_ADDR only, matching what django-ratelimit's ``key='ip'`` uses,
    so the rate limiter and Turnstile agree on who a caller is. Behind the nginx
    config in README.md that is the real client address because nginx sets
    X-Real-IP/X-Forwarded-For and gunicorn is trusted to forward them; if a
    deployment ever terminates elsewhere, fix it in the proxy config rather than
    trusting a client-supplied header here.
    """
    return request.META.get('REMOTE_ADDR', '') if request is not None else ''


def verify_turnstile(token, remote_ip=None) -> bool:
    """
    Validate a Cloudflare Turnstile token server-side.

    Returns True only when Cloudflare confirms the token. Anything else — no
    token, a rejected token, a network error, an unparseable response — returns
    False, so a broken or unreachable verification endpoint blocks signups
    rather than silently waving bots through.

    The one deliberate exception is local development: with DEBUG on and no
    TURNSTILE_SECRET configured there is nothing to verify against, so the check
    is skipped with a warning. With DEBUG off a missing secret still fails
    closed — a production box that forgot the env var should break loudly, not
    quietly lose its bot protection.
    """
    secret = os.getenv('TURNSTILE_SECRET', '')
    if not secret:
        if settings.DEBUG:
            logger.warning(
                'TURNSTILE_SECRET is not set; skipping Turnstile verification '
                '(DEBUG is on). Set the Cloudflare test keys for local dev.'
            )
            return True
        logger.error(
            'TURNSTILE_SECRET is not set; refusing signup because the '
            'verification check cannot be performed.'
        )
        return False

    if not token:
        return False

    payload = {'secret': secret, 'response': token}
    if remote_ip:
        payload['remoteip'] = remote_ip

    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL,
            data=payload,
            timeout=TURNSTILE_VERIFY_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        logger.exception('Turnstile verification request failed; rejecting signup')
        return False

    if result.get('success') is True:
        return True

    # 'error-codes' never contains the secret, so it is safe to log.
    logger.warning(
        'Turnstile verification rejected a signup: %s',
        result.get('error-codes', []),
    )
    return False


# ── Subscription pricing ─────────────────────────────────────────────────────

class PayPalPriceUpdateError(Exception):
    """Raised when PayPal refuses a price change, with a reason to show staff."""


def update_paypal_plan_price(plan, new_price):
    """
    Push a new price onto the PayPal billing plan behind ``plan``.

    Changing the number in our database on its own does nothing: PayPal keeps
    its own copy on the billing plan and charges from that, so the two have to
    move together or the site advertises one figure while PayPal collects
    another.

        POST /v1/billing/plans/{id}/update-pricing-schemes   ->  204 No Content

    ``billing_cycle_sequence: 1`` is the plan's regular cycle. These plans have
    no trial, so there is only ever the one sequence to reprice.

    **This reaches existing members**, not only new signups: PayPal applies the
    new price to everyone on the plan from their next billing date. That is why
    the caller refuses to save on failure rather than logging and carrying on —
    a silent failure leaves staff believing they changed what members pay when
    they did not.

    Raises PayPalPriceUpdateError with something worth showing an admin.
    Returns True on success.
    """
    from .paypal import PayPalError, paypal_request

    if not plan.paypal_plan_id:
        raise PayPalPriceUpdateError(
            'This plan has no PayPal plan ID yet, so there is nothing to '
            'update. Run `manage.py create_paypal_plans` first.'
        )

    payload = {
        'pricing_schemes': [
            {
                'billing_cycle_sequence': 1,
                'pricing_scheme': {
                    'fixed_price': {
                        'value': str(new_price),
                        'currency_code': plan.currency,
                    },
                },
            },
        ],
    }

    try:
        response = paypal_request(
            'POST',
            f'/v1/billing/plans/{plan.paypal_plan_id}/update-pricing-schemes',
            json=payload,
        )
    except PayPalError as exc:
        logger.exception('PayPal auth failed updating plan pricing')
        raise PayPalPriceUpdateError(f'Could not reach PayPal: {exc}') from exc
    except Exception as exc:
        logger.exception('Unexpected failure updating plan pricing')
        raise PayPalPriceUpdateError(f'Could not reach PayPal: {exc}') from exc

    if response.status_code not in (200, 204):
        logger.error(
            'PayPal rejected a price update for plan %s (%s): %s',
            plan.paypal_plan_id, response.status_code, response.text,
        )
        raise PayPalPriceUpdateError(
            f'PayPal rejected the change ({response.status_code}): '
            f'{response.text[:300]}'
        )

    logger.info(
        'PayPal plan %s repriced to %s %s',
        plan.paypal_plan_id, plan.currency, new_price,
    )
    return True
