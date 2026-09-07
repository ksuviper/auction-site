"""Tests for proxy (automatic) bidding."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    ProxyBid,
    Subscription,
)
from auctions.tests.utils import make_seller

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProxyBidTests(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('proxyseller', first_name='Proxy', last_name='Seller')
        self.listing = self._make_listing()
        self.proxy_url = reverse('place_proxy_bid', kwargs={'pk': self.listing.pk})
        self.bid_url = reverse('place_bid', kwargs={'pk': self.listing.pk})

    def _make_listing(self, start_price='10.00'):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Auction Daylily',
            category=self.category,
            seller=self.seller,
            start_price=start_price,
            listing_type='auction',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _user(self, username, country='US', subscribed=True):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        if subscribed:
            Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = country
        user.profile.save(update_fields=['country'])
        return user

    def test_proxy_bid_fires_immediately(self):
        b = self._user('bidder_b')
        self.client.force_login(b)
        self.client.post(self.proxy_url, {'max_amount': '20'})

        self.listing.refresh_from_db()
        # start_price 10, no bids -> auto bid = max(0, 10) + 1 = 11.
        self.assertEqual(self.listing.current_bid, Decimal('11.00'))
        top = Bid.objects.filter(listing=self.listing).order_by('-amount').first()
        self.assertEqual(top.bidder, b)
        self.assertEqual(top.amount, Decimal('11.00'))

    def test_two_competing_proxies_higher_max_wins(self):
        b = self._user('bidder_b')
        self.client.force_login(b)
        self.client.post(self.proxy_url, {'max_amount': '20'})

        c = self._user('bidder_c')
        self.client.force_login(c)
        self.client.post(self.proxy_url, {'max_amount': '30'})

        self.listing.refresh_from_db()
        # C wins at lower max ($20) + $1 = $21.
        self.assertEqual(self.listing.current_bid, Decimal('21.00'))
        top = Bid.objects.filter(listing=self.listing).order_by('-amount').first()
        self.assertEqual(top.bidder, c)
        self.assertEqual(top.amount, Decimal('21.00'))

    def test_proxy_bidder_outbid_by_manual_sends_email(self):
        b = self._user('bidder_b')
        self.client.force_login(b)
        self.client.post(self.proxy_url, {'max_amount': '20'})  # B leads at 11
        mail.outbox.clear()

        a = self._user('bidder_a')
        self.client.force_login(a)
        self.client.post(self.bid_url, {'amount': '25'})  # exceeds B's max

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.current_bid, Decimal('25.00'))
        b_proxy = ProxyBid.objects.get(listing=self.listing, bidder=b)
        self.assertFalse(b_proxy.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(b.email, mail.outbox[0].to)
        self.assertIn('outbid', mail.outbox[0].subject.lower())

    def test_proxy_below_current_bid_rejected(self):
        self.listing.current_bid = Decimal('15.00')
        self.listing.save(update_fields=['current_bid'])

        c = self._user('bidder_c')
        self.client.force_login(c)
        resp = self.client.post(self.proxy_url, {'max_amount': '10'})

        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.assertFalse(ProxyBid.objects.filter(listing=self.listing, bidder=c).exists())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.current_bid, Decimal('15.00'))

    def test_non_subscriber_redirected_to_subscribe(self):
        user = self._user('nosub', subscribed=False)
        self.client.force_login(user)
        resp = self.client.post(self.proxy_url, {'max_amount': '20'})
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)
        self.assertFalse(ProxyBid.objects.filter(bidder=user).exists())

    def test_non_us_user_blocked(self):
        user = self._user('cabidder', country='CA')
        self.client.force_login(user)
        resp = self.client.post(self.proxy_url, {'max_amount': '20'})
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.assertFalse(ProxyBid.objects.filter(bidder=user).exists())
