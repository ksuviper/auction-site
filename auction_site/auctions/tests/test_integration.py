"""End-to-end integration tests across subscriptions, bidding, buy-now,
proxy bidding, webhooks, and comments."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    Invoice,
    ListingComment,
    ProxyBid,
    Seller,
    Subscription,
)

User = get_user_model()
VERIFY_PATH = 'auctions.subscription_views.PayPalWebhookView._verify_signature'


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PAYPAL_WEBHOOK_ID='WH-TEST',
)
class IntegrationTests(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = Seller.objects.create(
            name='Seller', accepted_payment_methods='PayPal', shipping_fee='5.00',
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _user(self, username, country='US'):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        user.profile.country = country
        user.profile.save(update_fields=['country'])
        return user

    def _auction(self, start_price='10.00'):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Auction Lily', category=self.category, seller=self.seller,
            start_price=start_price, listing_type='auction',
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _buy_now(self, price='25.00'):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Buy Now Lily', category=self.category, seller=self.seller,
            start_price='10.00', listing_type='buy_now', buy_now_price=price,
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _webhook(self, event_type, sub_id, **resource):
        resource['id'] = sub_id
        return self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({'event_type': event_type, 'resource': resource}),
            content_type='application/json',
        )

    # ── subscription gating ─────────────────────────────────────────────────--

    def test_subscription_gates_bidding(self):
        user = self._user('gated')
        listing = self._auction()
        url = reverse('place_bid', kwargs={'pk': listing.pk})
        self.client.force_login(user)

        resp = self.client.post(url, {'amount': '15'})
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)

        Subscription.objects.create(user=user, plan='monthly', status='active')
        self.client.post(url, {'amount': '15'})
        self.assertTrue(Bid.objects.filter(listing=listing, bidder=user).exists())

    def test_subscription_override_allows_bidding(self):
        user = self._user('override')
        user.profile.subscription_required = False
        user.profile.save(update_fields=['subscription_required'])
        listing = self._auction()
        url = reverse('place_bid', kwargs={'pk': listing.pk})
        self.client.force_login(user)

        self.client.post(url, {'amount': '15'})
        self.assertTrue(Bid.objects.filter(listing=listing, bidder=user).exists())

        user.profile.subscription_required = True
        user.profile.save(update_fields=['subscription_required'])
        resp = self.client.post(url, {'amount': '20'})
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)

    def test_lapsed_grace_period(self):
        user = self._user('grace')
        sub = Subscription.objects.create(
            user=user, plan='monthly', status='lapsed',
            grace_period_end=timezone.now() + timedelta(days=1),
        )
        listing = self._auction()
        url = reverse('place_bid', kwargs={'pk': listing.pk})
        self.client.force_login(user)

        self.client.post(url, {'amount': '15'})
        self.assertTrue(Bid.objects.filter(listing=listing, bidder=user).exists())

        sub.grace_period_end = timezone.now() - timedelta(days=1)
        sub.save(update_fields=['grace_period_end'])
        resp = self.client.post(url, {'amount': '20'})
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)

    # ── buy now ────────────────────────────────────────────────────────────--

    def test_buy_now_race_condition(self):
        listing = self._buy_now()
        url = reverse('buy_now', kwargs={'pk': listing.pk})

        first = self._user('first')
        Subscription.objects.create(user=first, plan='monthly', status='active')
        self.client.force_login(first)
        self.client.post(url)

        second = self._user('second')
        Subscription.objects.create(user=second, plan='monthly', status='active')
        self.client.force_login(second)
        resp = self.client.post(url)

        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 1)
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': listing.pk}))

    # ── webhooks ──────────────────────────────────────────────────────────--

    @patch(VERIFY_PATH, return_value=True)
    def test_webhook_activates_subscription(self, _verify):
        user = self._user('whact')
        sub = Subscription.objects.create(
            user=user, plan='monthly', status='pending', paypal_subscription_id='SUB123',
        )
        resp = self._webhook('BILLING.SUBSCRIPTION.ACTIVATED', 'SUB123')
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')

    @patch(VERIFY_PATH, return_value=True)
    def test_webhook_payment_failed_sets_grace(self, _verify):
        user = self._user('whfail')
        sub = Subscription.objects.create(
            user=user, plan='monthly', status='active', paypal_subscription_id='SUBF',
        )
        resp = self._webhook('BILLING.SUBSCRIPTION.PAYMENT.FAILED', 'SUBF')
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'lapsed')
        self.assertGreater(sub.grace_period_end, timezone.now() + timedelta(days=2))
        self.assertLess(sub.grace_period_end, timezone.now() + timedelta(days=4))
        self.assertEqual(len(mail.outbox), 1)

    # ── proxy bidding ─────────────────────────────────────────────────────--

    def test_proxy_bid_outbids_manual_bidder(self):
        listing = self._auction(start_price='10.00')
        a = self._user('alice')
        Subscription.objects.create(user=a, plan='monthly', status='active')
        b = self._user('bob')
        Subscription.objects.create(user=b, plan='monthly', status='active')

        # A bids $10.
        self.client.force_login(a)
        self.client.post(reverse('place_bid', kwargs={'pk': listing.pk}), {'amount': '10'})

        # B sets a proxy max of $20 -> auto-bids to $11.
        self.client.force_login(b)
        self.client.post(reverse('place_proxy_bid', kwargs={'pk': listing.pk}), {'max_amount': '20'})
        listing.refresh_from_db()
        self.assertEqual(listing.current_bid, Decimal('11.00'))
        self.assertEqual(
            Bid.objects.filter(listing=listing, bidder=b).order_by('-amount').first().amount,
            Decimal('11.00'),
        )

        # A bids $15 -> proxy fires again to $16.
        self.client.force_login(a)
        self.client.post(reverse('place_bid', kwargs={'pk': listing.pk}), {'amount': '15'})
        listing.refresh_from_db()
        self.assertEqual(listing.current_bid, Decimal('16.00'))
        self.assertEqual(
            Bid.objects.filter(listing=listing, bidder=b).order_by('-amount').first().amount,
            Decimal('16.00'),
        )

    # ── comments ──────────────────────────────────────────────────────────--

    def test_comment_requires_login(self):
        listing = self._auction()
        resp = self.client.post(
            reverse('post_comment', kwargs={'pk': listing.pk}), {'body': 'hi'}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.headers['Location'])
        self.assertFalse(ListingComment.objects.filter(listing=listing).exists())
