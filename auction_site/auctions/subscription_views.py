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

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from .models import Subscription
from .paypal import PayPalError, paypal_request
from .utils import _safe_send

logger = logging.getLogger(__name__)


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
