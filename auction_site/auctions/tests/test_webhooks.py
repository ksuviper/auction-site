"""Tests for the PayPal subscription webhook handler.

Signature verification is mocked to return True so the tests exercise event
handling without real PayPal certificates. One test mocks it False to confirm
unverified requests are rejected with 400.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import Subscription

User = get_user_model()

VERIFY_PATH = 'auctions.subscription_views.PayPalWebhookView._verify_signature'


def _payload(event_type, sub_id, **resource_extra):
    resource = {'id': sub_id}
    resource.update(resource_extra)
    return {'event_type': event_type, 'resource': resource}


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


@override_settings(
    PAYPAL_WEBHOOK_ID='WH-TEST',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class PayPalWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('whuser', 'wh@example.com', 'pw')
        self.url = reverse('paypal_webhook')

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    @patch(VERIFY_PATH, return_value=True)
    def test_activated_sets_active_and_period_end(self, _verify):
        sub = Subscription.objects.create(
            user=self.user, plan='monthly', status='pending',
            paypal_subscription_id='SUB-ACT',
        )
        next_time = timezone.now() + timedelta(days=30)
        resp = self._post(_payload(
            'BILLING.SUBSCRIPTION.ACTIVATED', 'SUB-ACT',
            billing_info={'next_billing_time': _iso(next_time)},
        ))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')
        self.assertIsNotNone(sub.current_period_end)

    @patch(VERIFY_PATH, return_value=True)
    def test_renewed_updates_period_and_clears_grace(self, _verify):
        sub = Subscription.objects.create(
            user=self.user, plan='monthly', status='lapsed',
            paypal_subscription_id='SUB-REN',
            grace_period_end=timezone.now() + timedelta(days=1),
        )
        next_time = timezone.now() + timedelta(days=30)
        resp = self._post(_payload(
            'BILLING.SUBSCRIPTION.RENEWED', 'SUB-REN',
            billing_info={'next_billing_time': _iso(next_time)},
        ))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')
        self.assertIsNone(sub.grace_period_end)
        self.assertIsNotNone(sub.current_period_end)

    @patch(VERIFY_PATH, return_value=True)
    def test_payment_failed_sets_grace_and_emails(self, _verify):
        sub = Subscription.objects.create(
            user=self.user, plan='monthly', status='active',
            paypal_subscription_id='SUB-FAIL',
        )
        resp = self._post(_payload('BILLING.SUBSCRIPTION.PAYMENT.FAILED', 'SUB-FAIL'))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'lapsed')
        self.assertIsNotNone(sub.grace_period_end)
        # Grace ~ now + 3 days.
        self.assertGreater(sub.grace_period_end, timezone.now() + timedelta(days=2))
        self.assertLess(sub.grace_period_end, timezone.now() + timedelta(days=4))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('wh@example.com', mail.outbox[0].to)

    @patch(VERIFY_PATH, return_value=True)
    def test_cancelled_sets_cancelled_and_emails(self, _verify):
        sub = Subscription.objects.create(
            user=self.user, plan='yearly', status='active',
            paypal_subscription_id='SUB-CAN',
            current_period_end=timezone.now() + timedelta(days=100),
        )
        resp = self._post(_payload('BILLING.SUBSCRIPTION.CANCELLED', 'SUB-CAN'))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'cancelled')
        self.assertIsNotNone(sub.cancelled_at)
        self.assertEqual(len(mail.outbox), 1)

    @patch(VERIFY_PATH, return_value=True)
    def test_expired_sets_lapsed(self, _verify):
        sub = Subscription.objects.create(
            user=self.user, plan='monthly', status='active',
            paypal_subscription_id='SUB-EXP',
        )
        resp = self._post(_payload('BILLING.SUBSCRIPTION.EXPIRED', 'SUB-EXP'))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'lapsed')

    @patch(VERIFY_PATH, return_value=True)
    def test_unknown_event_acks_200(self, _verify):
        resp = self._post(_payload('BILLING.SUBSCRIPTION.SUSPENDED', 'SUB-X'))
        self.assertEqual(resp.status_code, 200)

    @patch(VERIFY_PATH, return_value=False)
    def test_invalid_signature_returns_400(self, _verify):
        resp = self._post(_payload('BILLING.SUBSCRIPTION.ACTIVATED', 'SUB-ACT'))
        self.assertEqual(resp.status_code, 400)
