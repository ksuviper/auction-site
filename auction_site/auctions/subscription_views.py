"""
User-facing PayPal subscription checkout flow.

Views:
  * SubscribeLandingView    GET  /subscribe/
  * SubscribeCreateView     POST /subscribe/create/<plan>/
  * SubscribeReturnView     GET  /subscribe/return/
  * SubscribeCancelledView  GET  /subscribe/cancelled/

All emails follow the _safe_send() helper pattern. Timezone-aware datetimes
are used throughout (TIME_ZONE=America/Chicago, USE_TZ=True).
"""

import json
import logging
from datetime import timedelta

import paypalrestsdk
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from .models import Subscription
from .paypal import PayPalError, paypal_request
from .utils import _safe_send

logger = logging.getLogger(__name__)


def _ensure_paypal_configured():
    """Lazily configure paypalrestsdk (used for webhook signature verification)."""
    if settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET:
        paypalrestsdk.configure({
            'mode': settings.PAYPAL_MODE,
            'client_id': settings.PAYPAL_CLIENT_ID,
            'client_secret': settings.PAYPAL_CLIENT_SECRET,
        })


class SubscribeLandingView(LoginRequiredMixin, TemplateView):
    """Show plan options, or the current membership state if one exists."""

    template_name = 'subscriptions/landing.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        subscription = getattr(self.request.user, 'subscription', None)

        try:
            monthly = float(settings.PAYPAL_MONTHLY_PRICE)
            yearly = float(settings.PAYPAL_YEARLY_PRICE)
            savings = round(monthly * 12 - yearly, 2)
        except (TypeError, ValueError):
            savings = None

        ctx.update({
            'subscription': subscription,
            'monthly_price': settings.PAYPAL_MONTHLY_PRICE,
            'yearly_price': settings.PAYPAL_YEARLY_PRICE,
            'yearly_savings': savings,
        })
        return ctx


class SubscribeCreateView(LoginRequiredMixin, View):
    """Create a PayPal subscription and redirect the user to PayPal to approve."""

    def post(self, request, plan):
        if plan not in ('monthly', 'yearly'):
            return HttpResponseBadRequest('Invalid plan.')

        plan_id = (
            settings.PAYPAL_MONTHLY_PLAN_ID if plan == 'monthly'
            else settings.PAYPAL_YEARLY_PLAN_ID
        )
        if not plan_id:
            messages.error(
                request,
                'Memberships are temporarily unavailable. Please try again later.',
            )
            return redirect('subscribe')

        payload = {
            'plan_id': plan_id,
            'application_context': {
                'brand_name': 'ASQ Daylily Auctions',
                'user_action': 'SUBSCRIBE_NOW',
                'return_url': request.build_absolute_uri(reverse('subscribe_return')),
                'cancel_url': request.build_absolute_uri(reverse('subscribe_cancelled')),
            },
        }

        try:
            resp = paypal_request('POST', '/v1/billing/subscriptions', json=payload)
        except PayPalError:
            logger.exception('PayPal auth failed creating subscription.')
            messages.error(request, 'We could not start the PayPal checkout. Please try again.')
            return redirect('subscribe')

        if resp.status_code not in (200, 201):
            logger.error('PayPal create subscription failed (%s): %s', resp.status_code, resp.text)
            messages.error(request, 'We could not start the PayPal checkout. Please try again.')
            return redirect('subscribe')

        data = resp.json()
        approval_url = next(
            (link['href'] for link in data.get('links', []) if link.get('rel') == 'approve'),
            None,
        )
        if not approval_url:
            logger.error('PayPal create subscription: no approval link in response %s', data)
            messages.error(request, 'We could not start the PayPal checkout. Please try again.')
            return redirect('subscribe')

        # update_or_create handles the OneToOne when a prior (cancelled/lapsed)
        # subscription record already exists for this user.
        Subscription.objects.update_or_create(
            user=request.user,
            defaults={
                'plan': plan,
                'status': 'pending',
                'paypal_subscription_id': data.get('id'),
                'paypal_plan_id': plan_id,
            },
        )
        return redirect(approval_url)


class SubscribeReturnView(LoginRequiredMixin, View):
    """PayPal redirects here after approval; confirm and activate."""

    def get(self, request):
        subscription_id = request.GET.get('subscription_id')
        subscription = getattr(request.user, 'subscription', None)

        if (
            not subscription_id
            or subscription is None
            or subscription.paypal_subscription_id != subscription_id
        ):
            messages.error(
                request,
                'We could not verify your subscription. '
                'Please contact us if you believe you were charged.',
            )
            return redirect('subscribe')

        try:
            resp = paypal_request('GET', f'/v1/billing/subscriptions/{subscription_id}')
        except PayPalError:
            logger.exception('PayPal auth failed verifying subscription.')
            messages.error(request, 'We could not verify your subscription right now. Please check back shortly.')
            return redirect('subscribe')

        if resp.status_code != 200:
            logger.error('PayPal get subscription failed (%s): %s', resp.status_code, resp.text)
            messages.error(request, 'We could not verify your subscription right now. Please check back shortly.')
            return redirect('subscribe')

        data = resp.json()
        if data.get('status') != 'ACTIVE':
            messages.warning(
                request,
                'Your membership is not active yet — it can take a moment to confirm. '
                'Please refresh shortly.',
            )
            return redirect('subscribe')

        now = timezone.now()
        days = 365 if subscription.plan == 'yearly' else 30
        subscription.status = 'active'
        subscription.started_at = now
        subscription.current_period_end = now + timedelta(days=days)
        subscription.grace_period_end = None
        subscription.save(update_fields=[
            'status', 'started_at', 'current_period_end', 'grace_period_end',
        ])

        self._send_welcome_email(request.user, subscription)
        messages.success(
            request,
            'Your membership is active! You can now bid on listings.',
        )
        return redirect('home')

    def _send_welcome_email(self, user, subscription):
        if not user.email:
            return
        renewal = (
            subscription.current_period_end.strftime('%B %d, %Y')
            if subscription.current_period_end else 'your next billing date'
        )
        body = f"""\
Welcome to ASQ Daylily Auctions!

Your {subscription.get_plan_display()} membership is now active.

Next renewal: {renewal}

You can now place bids and make purchases across the site. Thank you for
supporting the ASQ Daylily Auction Group!

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject='Welcome to ASQ Daylily Auctions — Membership Active',
            body=body,
            recipients=[user.email],
        )


class SubscribeCancelledView(LoginRequiredMixin, TemplateView):
    """Shown when the user backs out of the PayPal checkout."""

    template_name = 'subscriptions/cancelled.html'

    def get(self, request, *args, **kwargs):
        subscription = getattr(request.user, 'subscription', None)
        if subscription is not None and subscription.status == 'pending':
            subscription.delete()
        return super().get(request, *args, **kwargs)


class MembershipView(LoginRequiredMixin, View):
    """User-facing membership status & management page."""

    def get(self, request):
        subscription = getattr(request.user, 'subscription', None)
        if subscription is None:
            return redirect('subscribe')

        now = timezone.now()
        in_grace = (
            subscription.status == 'lapsed'
            and subscription.grace_period_end is not None
            and subscription.grace_period_end > now
        )
        can_cancel = subscription.status == 'active' or in_grace
        can_resubscribe = (
            subscription.status == 'cancelled'
            or (subscription.status == 'lapsed' and not in_grace)
        )
        return render(request, 'subscriptions/membership.html', {
            'subscription': subscription,
            'in_grace': in_grace,
            'can_cancel': can_cancel,
            'can_resubscribe': can_resubscribe,
        })


class MembershipCancelView(LoginRequiredMixin, View):
    """Cancel the user's PayPal subscription."""

    def post(self, request):
        subscription = getattr(request.user, 'subscription', None)
        if subscription is None or subscription.status not in ('active', 'lapsed'):
            messages.error(request, 'You do not have an active membership to cancel.')
            return redirect('membership')

        sub_id = subscription.paypal_subscription_id
        if not sub_id:
            messages.error(
                request,
                'We could not locate your PayPal subscription. Please contact us.',
            )
            return redirect('membership')

        try:
            resp = paypal_request(
                'POST',
                f'/v1/billing/subscriptions/{sub_id}/cancel',
                json={'reason': 'Cancelled by user'},
            )
        except PayPalError:
            logger.exception('PayPal auth failed cancelling subscription.')
            messages.error(request, 'We could not cancel your membership right now. Please try again.')
            return redirect('membership')

        # PayPal returns 204 No Content on a successful cancel.
        if resp.status_code not in (200, 204):
            logger.error('PayPal cancel subscription failed (%s): %s', resp.status_code, resp.text)
            messages.error(request, 'We could not cancel your membership right now. Please try again.')
            return redirect('membership')

        subscription.status = 'cancelled'
        subscription.cancelled_at = timezone.now()
        subscription.save(update_fields=['status', 'cancelled_at'])
        self._email_cancelled(request.user, subscription)
        messages.success(request, 'Your membership has been cancelled.')
        return redirect('membership')

    def _email_cancelled(self, user, subscription):
        if not user.email:
            return
        if subscription.current_period_end and subscription.current_period_end > timezone.now():
            access_line = (
                f'You have access until '
                f'{subscription.current_period_end.strftime("%B %d, %Y")}.'
            )
        else:
            access_line = 'Your access has ended.'
        body = f"""\
Your ASQ Daylily Auctions membership has been cancelled.

{access_line}

You can re-subscribe at any time from the Membership page. Thank you for being
part of the ASQ Daylily Auction Group.

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject='Your ASQ Daylily Auctions membership has been cancelled',
            body=body,
            recipients=[user.email],
        )


@method_decorator(csrf_exempt, name='dispatch')
class PayPalWebhookView(View):
    """
    Receive PayPal subscription webhooks and keep local Subscription records in
    sync. PayPal cannot send a CSRF token, so this endpoint is CSRF-exempt and
    instead verifies PayPal's transmission signature before processing.
    """

    def post(self, request, *args, **kwargs):
        if not self._verify_signature(request):
            logger.warning('PayPal webhook: signature verification failed.')
            return HttpResponse(status=400)

        try:
            event = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            logger.warning('PayPal webhook: invalid JSON body.')
            return HttpResponse(status=400)

        event_type = event.get('event_type', '')
        resource = event.get('resource') or {}

        handler = {
            'BILLING.SUBSCRIPTION.ACTIVATED': self._handle_activated,
            'BILLING.SUBSCRIPTION.RENEWED': self._handle_renewed,
            'BILLING.SUBSCRIPTION.PAYMENT.FAILED': self._handle_payment_failed,
            'BILLING.SUBSCRIPTION.CANCELLED': self._handle_cancelled,
            'BILLING.SUBSCRIPTION.EXPIRED': self._handle_expired,
        }.get(event_type)

        if handler is None:
            logger.debug('PayPal webhook: unhandled event type %s', event_type)
            return HttpResponse(status=200)

        try:
            handler(resource)
        except Subscription.DoesNotExist:
            # Nothing to retry — ack so PayPal stops resending.
            logger.warning(
                'PayPal webhook %s: no subscription for id %s',
                event_type, resource.get('id'),
            )
        except Exception:
            # Unexpected/transient failure — return non-200 so PayPal retries.
            logger.exception('PayPal webhook %s: handler error', event_type)
            return HttpResponse(status=500)

        return HttpResponse(status=200)

    # ── Signature verification ────────────────────────────────────────────────

    def _verify_signature(self, request):
        webhook_id = settings.PAYPAL_WEBHOOK_ID
        if not webhook_id:
            logger.error('PAYPAL_WEBHOOK_ID is not configured; rejecting webhook.')
            return False

        try:
            transmission_id = request.headers['Paypal-Transmission-Id']
            timestamp = request.headers['Paypal-Transmission-Time']
            cert_url = request.headers['Paypal-Cert-Url']
            auth_algo = request.headers['Paypal-Auth-Algo']
            actual_sig = request.headers['Paypal-Transmission-Sig']
        except KeyError:
            logger.warning('PayPal webhook: missing signature headers.')
            return False

        _ensure_paypal_configured()
        try:
            return bool(paypalrestsdk.WebhookEvent.verify(
                transmission_id,
                timestamp,
                webhook_id,
                request.body.decode('utf-8'),
                cert_url,
                actual_sig,
                auth_algo,
            ))
        except Exception:
            logger.exception('PayPal webhook: signature verification raised.')
            return False

    # ── Event handlers ──────────────────────────────────────────────────────--

    @staticmethod
    def _next_billing_time(resource):
        raw = (resource.get('billing_info') or {}).get('next_billing_time')
        return parse_datetime(raw) if raw else None

    def _handle_activated(self, resource):
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update()
                .get(paypal_subscription_id=resource.get('id'))
            )
            sub.status = 'active'
            fields = ['status']
            next_billing = self._next_billing_time(resource)
            if next_billing:
                sub.current_period_end = next_billing
                fields.append('current_period_end')
            sub.save(update_fields=fields)

    def _handle_renewed(self, resource):
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update()
                .get(paypal_subscription_id=resource.get('id'))
            )
            # A renewal is a successful payment, so the membership is active and
            # any prior grace period no longer applies.
            sub.status = 'active'
            sub.grace_period_end = None
            fields = ['status', 'grace_period_end']
            next_billing = self._next_billing_time(resource)
            if next_billing:
                sub.current_period_end = next_billing
                fields.append('current_period_end')
            sub.save(update_fields=fields)

    def _handle_payment_failed(self, resource):
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update().select_related('user')
                .get(paypal_subscription_id=resource.get('id'))
            )
            sub.status = 'lapsed'
            sub.grace_period_end = timezone.now() + timedelta(days=3)
            sub.save(update_fields=['status', 'grace_period_end'])
        self._email_payment_failed(sub)

    def _handle_cancelled(self, resource):
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update().select_related('user')
                .get(paypal_subscription_id=resource.get('id'))
            )
            # Idempotent: if already cancelled (e.g. the user cancelled on-site,
            # or PayPal retried this webhook), don't re-stamp or re-email.
            already_cancelled = sub.status == 'cancelled'
            if not already_cancelled:
                sub.status = 'cancelled'
                sub.cancelled_at = sub.cancelled_at or timezone.now()
                sub.save(update_fields=['status', 'cancelled_at'])
        if not already_cancelled:
            self._email_cancelled(sub)

    def _handle_expired(self, resource):
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update()
                .get(paypal_subscription_id=resource.get('id'))
            )
            sub.status = 'lapsed'
            sub.save(update_fields=['status'])

    # ── Webhook emails ────────────────────────────────────────────────────────

    def _email_payment_failed(self, sub):
        user = sub.user
        if not user.email:
            return
        grace = (
            sub.grace_period_end.strftime('%B %d, %Y')
            if sub.grace_period_end else 'a short grace period'
        )
        body = f"""\
Your ASQ Daylily Auctions payment failed.

You have access until {grace}. Please update your payment method in PayPal to
keep your membership active and avoid losing your bidding privileges.

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject='Your ASQ Daylily Auctions payment failed',
            body=body,
            recipients=[user.email],
        )

    def _email_cancelled(self, sub):
        user = sub.user
        if not user.email:
            return
        if sub.current_period_end and sub.current_period_end > timezone.now():
            access_line = (
                f'Your access continues until '
                f'{sub.current_period_end.strftime("%B %d, %Y")}.'
            )
        else:
            access_line = 'Your access has ended.'
        body = f"""\
Your ASQ Daylily Auctions membership has been cancelled.

{access_line}

You can re-subscribe at any time from the Membership page. Thank you for being
part of the ASQ Daylily Auction Group.

-- ASQ Daylily Auction Group
"""
        _safe_send(
            subject='Your ASQ Daylily Auctions membership was cancelled',
            body=body,
            recipients=[user.email],
        )
