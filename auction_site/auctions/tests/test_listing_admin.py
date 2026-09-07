"""Tests for copying listings and for freezing ones that have sold.

Two rules are easy to get backwards and both matter:

  * A copy must not inherit the original's *run* — its dates, its stock level,
    its bids, its winner. Inheriting any of those quietly relists something as
    already half-sold, or live before anyone meant it to be.
  * "Ended" is not "sold". A listing that expired with nobody bidding has sold
    nothing and stays fully editable, so an admin can re-date it and run it
    again; only a listing somebody committed to buy gets frozen, because its
    title and price are what an invoice says they agreed to.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing, Invoice
from auctions.services import copy_listing
from auctions.tests.utils import make_seller

User = get_user_model()


class ListingFixtureMixin:
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('brynn', first_name='Brynn', last_name='Vale')
        self.buyer = User.objects.create_user('cass', 'cass@example.com', 'pw')

    def _listing(self, **kwargs):
        now = timezone.now()
        defaults = dict(
            title='Original Plant',
            description='A good doer.',
            category=self.category,
            seller=self.seller,
            start_price='10.00',
            reserve_price='15.00',
            starts_at=now - timedelta(days=3),
            ends_at=now + timedelta(days=4),
            is_active=True,
        )
        defaults.update(kwargs)
        listing = AuctionListing.objects.create(**defaults)
        # objects.create() leaves the strings assigned above on the instance;
        # every real caller reads a listing back from the database and gets
        # Decimals. Refreshing keeps these tests honest about what copy_listing
        # is actually handed.
        listing.refresh_from_db()
        return listing

    def _buy_now(self, **kwargs):
        return self._listing(
            listing_type='buy_now', buy_now_price='20.00',
            quantity_available=6, shipping_mode='per_item',
            shipping_fee='4.00', **kwargs,
        )

    def _invoice(self, listing, **kwargs):
        defaults = dict(
            listing=listing, buyer=self.buyer, seller=self.seller,
            quantity=1, amount='20.00', shipping_fee='4.00',
        )
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)


class CopyListingTests(ListingFixtureMixin, TestCase):
    def test_it_carries_the_plant_details_across(self):
        source = self._buy_now()

        copy = copy_listing(source)

        self.assertEqual(copy.title, 'Original Plant')
        self.assertEqual(copy.description, 'A good doer.')
        self.assertEqual(copy.category_id, self.category.pk)
        self.assertEqual(copy.seller_id, self.seller.pk)
        self.assertEqual(copy.listing_type, 'buy_now')
        self.assertEqual(copy.buy_now_price, Decimal('20.00'))
        self.assertEqual(copy.reserve_price, Decimal('15.00'))
        self.assertEqual(copy.quantity_available, 6)
        self.assertEqual(copy.shipping_mode, 'per_item')
        self.assertEqual(copy.shipping_fee, Decimal('4.00'))

    def test_it_clears_the_dates(self):
        """New dates are the whole reason to copy something."""
        copy = copy_listing(self._listing())

        self.assertIsNone(copy.starts_at)
        self.assertIsNone(copy.ends_at)

    def test_it_starts_inactive_and_open(self):
        source = self._listing(is_closed=True, is_active=False)

        copy = copy_listing(source)

        self.assertFalse(copy.is_closed)
        self.assertFalse(copy.is_active)

    def test_it_drops_the_winner_and_the_bidding_history(self):
        source = self._listing(winner=self.buyer, current_bid='42.00')

        copy = copy_listing(source)

        self.assertIsNone(copy.winner)
        self.assertEqual(copy.current_bid, 0)

    def test_it_restocks_rather_than_inheriting_what_sold(self):
        source = self._buy_now()
        AuctionListing.objects.filter(pk=source.pk).update(quantity_remaining=1)
        source.refresh_from_db()

        copy = copy_listing(source)

        self.assertEqual(copy.quantity_remaining, 6)

    def test_it_leaves_auction_stock_alone(self):
        copy = copy_listing(self._listing())

        self.assertIsNone(copy.quantity_available)
        self.assertIsNone(copy.quantity_remaining)

    def test_it_returns_something_unsaved(self):
        """The caller decides when it exists; the model demands dates first."""
        copy = copy_listing(self._listing())

        self.assertIsNone(copy.pk)
        self.assertEqual(AuctionListing.objects.count(), 1)

    def test_the_copy_shares_the_original_image_rather_than_a_new_file(self):
        source = self._listing(image='listings/rowan.jpg')

        copy = copy_listing(source)

        self.assertEqual(copy.image.name, 'listings/rowan.jpg')


class DuplicateActionTests(ListingFixtureMixin, TestCase):
    """The admin action is a thin wrapper over copy_listing()."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            'dupeboss', 'dupeboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)
        self.url = reverse('admin:auctions_auctionlisting_changelist')

    def _duplicate(self, *listings):
        return self.client.post(self.url, {
            'action': 'duplicate_listings',
            '_selected_action': [str(x.pk) for x in listings],
        })

    def test_duplicating_one_listing_opens_the_copy_for_editing(self):
        source = self._listing()

        response = self._duplicate(source)

        copy = AuctionListing.objects.exclude(pk=source.pk).get()
        self.assertRedirects(
            response,
            reverse('admin:auctions_auctionlisting_change', args=[copy.pk]),
            fetch_redirect_response=False,
        )

    def test_the_copy_is_inactive_so_it_cannot_go_live_unnoticed(self):
        source = self._listing()

        self._duplicate(source)

        copy = AuctionListing.objects.exclude(pk=source.pk).get()
        self.assertFalse(copy.is_active)
        self.assertFalse(copy.is_closed)

    def test_the_copy_is_restocked(self):
        source = self._buy_now()
        AuctionListing.objects.filter(pk=source.pk).update(quantity_remaining=2)

        self._duplicate(source)

        copy = AuctionListing.objects.exclude(pk=source.pk).get()
        self.assertEqual(copy.quantity_remaining, 6)

    def test_duplicating_several_stays_on_the_list(self):
        """There is no single change page to send an admin to."""
        first, second = self._listing(title='One'), self._listing(title='Two')

        response = self._duplicate(first, second)

        self.assertEqual(AuctionListing.objects.count(), 4)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.url)

    def test_a_sold_listing_can_still_be_duplicated(self):
        """Freezing a sold listing must not block relisting the same plant."""
        source = self._buy_now()
        self._invoice(source)

        self._duplicate(source)

        self.assertEqual(AuctionListing.objects.exclude(pk=source.pk).count(), 1)


class SoldListingLockTests(ListingFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            'lockboss', 'lockboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)

    def _change_url(self, listing):
        return reverse('admin:auctions_auctionlisting_change', args=[listing.pk])

    def _post_a_new_title(self, listing, title='Renamed'):
        return self.client.post(self._change_url(listing), {
            'title': title,
            'category': self.category.pk,
            'seller': self.seller.pk,
            'description': listing.description,
            'start_price': listing.start_price,
            'current_bid': listing.current_bid,
            'listing_type': listing.listing_type,
            'shipping_mode': listing.shipping_mode,
            'starts_at_0': listing.starts_at.date().isoformat(),
            'starts_at_1': '00:00:00',
            'ends_at_0': listing.ends_at.date().isoformat(),
            'ends_at_1': '00:00:00',
        }, follow=True)

    # ── has_completed_sale ───────────────────────────────────────────────────

    def test_an_auction_with_a_winner_counts_as_sold(self):
        listing = self._listing(winner=self.buyer, is_closed=True)

        self.assertTrue(listing.has_completed_sale)

    def test_an_auction_that_expired_unsold_does_not(self):
        now = timezone.now()
        listing = self._listing(
            starts_at=now - timedelta(days=9), ends_at=now - timedelta(days=2),
            is_closed=True, is_active=False,
        )

        self.assertFalse(listing.has_completed_sale)

    def test_a_buy_now_listing_with_an_invoice_counts_as_sold(self):
        listing = self._buy_now()
        self._invoice(listing)

        self.assertTrue(listing.has_completed_sale)

    def test_a_sold_out_buy_now_listing_with_no_invoice_does_not(self):
        """Stock can be zeroed by an admin without anyone having bought."""
        listing = self._buy_now()
        AuctionListing.objects.filter(pk=listing.pk).update(quantity_remaining=0)
        listing.refresh_from_db()

        self.assertFalse(listing.has_completed_sale)

    # ── The admin lock ───────────────────────────────────────────────────────

    def test_a_sold_listing_cannot_be_edited_through_the_admin(self):
        listing = self._listing(winner=self.buyer, is_closed=True)

        self._post_a_new_title(listing, 'Should Not Stick')

        listing.refresh_from_db()
        self.assertEqual(listing.title, 'Original Plant')

    def test_a_sold_listing_is_still_readable_in_the_admin(self):
        """Invoices point at it, so the record has to stay reachable."""
        listing = self._buy_now()
        self._invoice(listing)

        response = self.client.get(self._change_url(listing))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Original Plant')

    def test_a_sold_listing_cannot_be_deleted_through_the_admin(self):
        listing = self._buy_now()
        self._invoice(listing)

        response = self.client.post(
            reverse('admin:auctions_auctionlisting_delete', args=[listing.pk]),
            {'post': 'yes'},
        )

        self.assertNotEqual(response.status_code, 302)
        self.assertTrue(AuctionListing.objects.filter(pk=listing.pk).exists())

    def test_an_expired_unsold_listing_can_be_re_dated_and_reactivated(self):
        now = timezone.now()
        listing = self._listing(
            starts_at=now - timedelta(days=9), ends_at=now - timedelta(days=2),
            is_closed=True, is_active=False,
        )

        response = self.client.post(self._change_url(listing), {
            'title': 'Second Run',
            'category': self.category.pk,
            'seller': self.seller.pk,
            'description': '',
            'start_price': '10.00',
            'current_bid': '0',
            'listing_type': 'auction',
            'shipping_mode': 'flat',
            'starts_at_0': (now + timedelta(days=1)).date().isoformat(),
            'starts_at_1': '09:00:00',
            'ends_at_0': (now + timedelta(days=8)).date().isoformat(),
            'ends_at_1': '09:00:00',
            'is_active': 'on',
        }, follow=True)

        listing.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(listing.title, 'Second Run')
        self.assertTrue(listing.is_active)
        self.assertGreater(listing.ends_at, now)

    def test_the_changelist_stays_editable_when_a_sold_listing_exists(self):
        """obj is None there; denying would take the whole model read-only."""
        sold = self._buy_now()
        self._invoice(sold)

        response = self.client.get(
            reverse('admin:auctions_auctionlisting_changelist')
        )

        self.assertEqual(response.status_code, 200)
        # The actions dropdown is only rendered with change permission.
        self.assertContains(response, 'duplicate_listings')
