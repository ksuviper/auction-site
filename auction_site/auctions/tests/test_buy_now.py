"""Tests for the Buy It Now purchase flow."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Invoice,
    Seller,
    Subscription,
)

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowTests(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = Seller.objects.create(
            name='Test Seller',
            accepted_payment_methods='PayPal',
            shipping_fee='5.00',
        )
        self.listing = self._make_listing()
        self.url = reverse('buy_now', kwargs={'pk': self.listing.pk})

    def _make_listing(self):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Buy Now Daylily',
            category=self.category,
            seller=self.seller,
            start_price='10.00',
            listing_type='buy_now',
            buy_now_price='25.00',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _subscribed_user(self, username, country='US'):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        # Buy-now is US-only, same as bidding.
        user.profile.country = country
        user.profile.save(update_fields=['country'])
        return user

    def test_successful_purchase_closes_listing_and_creates_invoice(self):
        buyer = self._subscribed_user('buyer1')
        self.client.force_login(buyer)
        resp = self.client.post(self.url)

        self.listing.refresh_from_db()
        self.assertTrue(self.listing.is_closed)
        self.assertFalse(self.listing.is_active)
        self.assertEqual(self.listing.winner, buyer)

        invoice = Invoice.objects.get(listing=self.listing)
        self.assertEqual(invoice.buyer, buyer)
        self.assertEqual(invoice.seller, self.seller)
        self.assertEqual(str(invoice.amount), '25.00')
        self.assertRedirects(resp, reverse('invoice_detail', kwargs={'pk': invoice.pk}))

    def test_second_purchase_attempt_is_blocked(self):
        first = self._subscribed_user('buyer_first')
        self.client.force_login(first)
        self.client.post(self.url)
        self.assertEqual(Invoice.objects.filter(listing=self.listing).count(), 1)

        second = self._subscribed_user('buyer_second')
        self.client.force_login(second)
        resp = self.client.post(self.url)

        # No second invoice, and the buyer is sent back to the listing.
        self.assertEqual(Invoice.objects.filter(listing=self.listing).count(), 1)
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.winner, first)

    def test_non_subscriber_redirected_to_subscribe(self):
        user = User.objects.create_user('nosub', 'nosub@example.com', 'pw')
        self.client.force_login(user)
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())

    def test_non_us_user_blocked(self):
        user = self._subscribed_user('cabuyer', country='CA')
        self.client.force_login(user)
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())

    def test_unauthenticated_redirected_to_login(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.headers['Location'])
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())
