"""Tests for sellers being User accounts rather than records of their own.

Two things are load-bearing here and neither is visible from the happy path:

  * A seller-shaped URL must not render for an account that is not a seller.
    /seller/<pk>/ used to take a Seller pk, where every row was by definition a
    seller; it now takes a User pk, so without the is_seller filter it would
    publish a page for any account whose number was guessed.
  * The dashboard must only ever show its own owner's sales. It is the first
    page in this project where one member sees money figures at all.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing, Invoice, UserProfile
from auctions.tests.utils import make_seller

User = get_user_model()


class SellerMergeTestCase(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller(
            'rowan', first_name='Rowan', last_name='Fields',
            category=None, shipping_fee='6.00',
        )

    def _listing(self, seller=None, *, title='Plant', ended=False, **kwargs):
        now = timezone.now()
        defaults = dict(
            title=title,
            category=self.category,
            seller=seller or self.seller,
            start_price='10.00',
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1) if ended else now + timedelta(days=5),
            is_active=not ended,
            is_closed=ended,
        )
        defaults.update(kwargs)
        return AuctionListing.objects.create(**defaults)


class SellerIdentityTests(SellerMergeTestCase):
    def test_display_name_prefers_the_full_name(self):
        self.assertEqual(self.seller.profile.display_name, 'Rowan Fields')

    def test_display_name_falls_back_to_the_username(self):
        nameless = make_seller('quill')
        self.assertEqual(nameless.profile.display_name, 'quill')

    def test_a_listing_reads_its_shipping_fee_from_the_seller_profile(self):
        listing = self._listing(listing_type='buy_now', buy_now_price='20.00')

        self.assertEqual(listing.shipping_rate, Decimal('6.00'))

    def test_a_seller_with_no_standard_fee_ships_free_rather_than_erroring(self):
        """seller_shipping_fee is nullable, and shipping_rate must not blow up."""
        UserProfile.objects.filter(user=self.seller).update(seller_shipping_fee=None)
        listing = self._listing(listing_type='buy_now', buy_now_price='20.00')
        listing.refresh_from_db()

        self.assertEqual(listing.shipping_rate, Decimal('0'))

    def test_a_seller_holding_listings_cannot_be_deleted(self):
        """on_delete=PROTECT — losing a seller would orphan sold-plant records."""
        from django.db.models import ProtectedError

        self._listing()

        with self.assertRaises(ProtectedError):
            self.seller.delete()


class SellerPublicPageTests(SellerMergeTestCase):
    def test_it_shows_the_sellers_open_listings(self):
        self._listing(title='Open Now')
        self._listing(title='Long Gone', ended=True)

        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': self.seller.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rowan Fields')
        self.assertContains(response, 'Open Now')
        self.assertNotContains(response, 'Long Gone')

    def test_a_non_seller_account_is_not_published_at_a_seller_url(self):
        buyer = User.objects.create_user('nosy', 'nosy@example.com', 'pw')

        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': buyer.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_revoking_the_seller_flag_takes_the_page_down(self):
        UserProfile.objects.filter(user=self.seller).update(is_seller=False)

        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': self.seller.pk})
        )

        self.assertEqual(response.status_code, 404)


class SellerDashboardTests(SellerMergeTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('seller_dashboard')
        self.buyer = User.objects.create_user('buyer', 'buyer@example.com', 'pw')

    def _sale(self, listing, seller=None, amount='30.00', quantity=3):
        return Invoice.objects.create(
            listing=listing,
            buyer=self.buyer,
            seller=seller or self.seller,
            quantity=quantity,
            amount=amount,
            shipping_fee='6.00',
        )

    def test_anonymous_visitors_are_sent_to_log_in(self):
        response = self.client.get(self.url)

        self.assertNotEqual(response.status_code, 200)

    def test_a_non_seller_cannot_reach_it_even_by_url(self):
        self.client.force_login(self.buyer)

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('home'))

    def test_a_seller_sees_their_own_active_listings(self):
        self._listing(title='My Open Plant')
        self.client.force_login(self.seller)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Open Plant')
        self.assertEqual(response.context['active_count'], 1)

    def test_a_seller_sees_their_own_sales_and_totals(self):
        listing = self._listing(title='Sold Plant', ended=True)
        self._sale(listing, amount='30.00', quantity=3)
        self.client.force_login(self.seller)

        response = self.client.get(self.url)

        self.assertContains(response, 'Sold Plant')
        self.assertEqual(response.context['units_sold'], 3)
        self.assertEqual(response.context['total_revenue'], Decimal('30.00'))

    def test_a_seller_never_sees_another_sellers_data(self):
        other = make_seller('mallory', first_name='Mallory')
        theirs = self._listing(seller=other, title='Not Yours')
        self._sale(theirs, seller=other, amount='500.00', quantity=9)
        self.client.force_login(self.seller)

        response = self.client.get(self.url)

        self.assertNotContains(response, 'Not Yours')
        self.assertEqual(response.context['active_count'], 0)
        self.assertEqual(response.context['units_sold'], 0)
        self.assertEqual(response.context['total_revenue'], Decimal('0.00'))

    def test_it_offers_nothing_to_edit(self):
        """Read-only by policy: sellers do not manage their own listings."""
        self._listing(title='Look Only')
        self.client.force_login(self.seller)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('<form', html.lower())
        self.assertNotIn('/admin/', html)

    def test_a_seller_with_nothing_yet_gets_a_page_not_an_error(self):
        self.client.force_login(self.seller)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_revenue'], Decimal('0.00'))

    def test_the_nav_links_it_only_for_sellers(self):
        self.client.force_login(self.seller)
        self.assertContains(self.client.get(reverse('home')), self.url)

        self.client.force_login(self.buyer)
        self.assertNotContains(self.client.get(reverse('home')), self.url)
