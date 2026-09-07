"""Tests for the Buy It Now purchase flow, including multi-quantity stock."""

import threading
from datetime import timedelta
from io import StringIO
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import (
    Client,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from auctions.forms import BuyNowForm
from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Invoice,
    Subscription,
)
from auctions.tests.utils import make_seller

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowTests(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('testseller', first_name='Test', last_name='Seller')
        self.listing = self._make_listing()
        self.url = reverse('buy_now', kwargs={'pk': self.listing.pk})

    def _make_listing(self, quantity=1, shipping_mode='flat'):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Buy Now Daylily',
            category=self.category,
            seller=self.seller,
            start_price='10.00',
            listing_type='buy_now',
            buy_now_price='25.00',
            quantity_available=quantity,
            shipping_mode=shipping_mode,
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
        resp = self.client.post(self.url, {'quantity': 1})

        self.listing.refresh_from_db()
        self.assertTrue(self.listing.is_closed)
        self.assertFalse(self.listing.is_active)
        # `winner` is auction-only now: a buy_now listing can have many buyers,
        # so who bought what lives in the Invoice rows.
        self.assertIsNone(self.listing.winner)

        invoice = Invoice.objects.get(listing=self.listing)
        self.assertEqual(invoice.buyer, buyer)
        self.assertEqual(invoice.seller, self.seller)
        self.assertEqual(str(invoice.amount), '25.00')
        self.assertRedirects(resp, reverse('invoice_detail', kwargs={'pk': invoice.pk}))

    def test_second_purchase_attempt_is_blocked(self):
        first = self._subscribed_user('buyer_first')
        self.client.force_login(first)
        self.client.post(self.url, {'quantity': 1})
        self.assertEqual(Invoice.objects.filter(listing=self.listing).count(), 1)

        second = self._subscribed_user('buyer_second')
        self.client.force_login(second)
        resp = self.client.post(self.url, {'quantity': 1})

        # No second invoice, and the buyer is sent back to the listing.
        self.assertEqual(Invoice.objects.filter(listing=self.listing).count(), 1)
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.units_remaining, 0)

    def test_non_subscriber_redirected_to_subscribe(self):
        user = User.objects.create_user('nosub', 'nosub@example.com', 'pw')
        self.client.force_login(user)
        resp = self.client.post(self.url, {'quantity': 1})
        self.assertRedirects(resp, reverse('subscribe'), fetch_redirect_response=False)
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())

    def test_non_us_user_blocked(self):
        user = self._subscribed_user('cabuyer', country='CA')
        self.client.force_login(user)
        resp = self.client.post(self.url, {'quantity': 1})
        self.assertRedirects(resp, reverse('listing_detail', kwargs={'pk': self.listing.pk}))
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())

    def test_unauthenticated_redirected_to_login(self):
        resp = self.client.post(self.url, {'quantity': 1})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.headers['Location'])
        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MultiQuantityBuyNowTests(TestCase):
    """Several buyers can each take part of a listing's stock until it runs out."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('testseller', first_name='Test', last_name='Seller')

    def _listing(self, quantity=5, shipping_mode='flat', price='12.00'):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Stocked Daylily',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price=price,
            quantity_available=quantity,
            shipping_mode=shipping_mode,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _buyer(self, username):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = 'US'
        user.profile.save(update_fields=['country'])
        return user

    def _buy(self, listing, username, quantity):
        client = Client()
        client.force_login(self._buyer(username))
        return client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': quantity}
        )

    # ── Stock accounting ────────────────────────────────────────────────────

    def test_creation_mirrors_remaining_from_available(self):
        listing = self._listing(quantity=5)
        self.assertEqual(listing.quantity_remaining, 5)
        self.assertEqual(listing.units_remaining, 5)
        self.assertFalse(listing.is_sold_out)

    def test_partial_purchase_leaves_the_listing_open(self):
        listing = self._listing(quantity=5)

        self._buy(listing, 'buyer_a', 2)

        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 3)
        self.assertTrue(listing.is_active)
        self.assertFalse(listing.is_closed)
        self.assertFalse(listing.is_sold_out)

    def test_second_buyer_takes_the_rest_and_closes_it(self):
        listing = self._listing(quantity=5)

        self._buy(listing, 'buyer_a', 2)
        self._buy(listing, 'buyer_b', 3)

        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 0)
        self.assertTrue(listing.is_closed)
        self.assertFalse(listing.is_active)
        self.assertTrue(listing.is_sold_out)
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 2)

    def test_buying_after_sold_out_is_refused(self):
        listing = self._listing(quantity=2)
        self._buy(listing, 'buyer_a', 2)

        response = self._buy(listing, 'buyer_c', 1)

        self.assertRedirects(
            response, reverse('listing_detail', kwargs={'pk': listing.pk})
        )
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 1)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 0)

    def test_requesting_more_than_remains_creates_no_invoice(self):
        listing = self._listing(quantity=3)
        self._buy(listing, 'buyer_a', 2)

        response = self._buy(listing, 'buyer_b', 2)

        self.assertRedirects(
            response, reverse('listing_detail', kwargs={'pk': listing.pk})
        )
        # Not a partial sale: one unit was left, and two were asked for.
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 1)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 1)
        self.assertTrue(listing.is_active)

    def test_error_message_names_the_remaining_count(self):
        listing = self._listing(quantity=3)
        self._buy(listing, 'buyer_a', 2)

        client = Client()
        client.force_login(self._buyer('buyer_b'))
        response = client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}),
            {'quantity': 2},
            follow=True,
        )

        self.assertContains(response, 'only 1 remaining')

    def test_quantity_below_one_is_refused(self):
        listing = self._listing(quantity=5)

        self._buy(listing, 'buyer_a', 0)

        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 0)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 5)

    def test_negative_quantity_cannot_inflate_stock(self):
        """A negative quantity must not be treated as a refund."""
        listing = self._listing(quantity=5)

        self._buy(listing, 'buyer_a', -3)

        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 0)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 5)

    # ── Invoice contents ────────────────────────────────────────────────────

    def test_invoice_records_quantity_and_line_total(self):
        listing = self._listing(quantity=5, price='12.00')

        self._buy(listing, 'buyer_a', 3)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(invoice.quantity, 3)
        self.assertEqual(str(invoice.amount), '36.00')

    def test_flat_shipping_is_charged_once(self):
        listing = self._listing(quantity=5, shipping_mode='flat')

        self._buy(listing, 'buyer_a', 4)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(str(invoice.shipping_fee), '5.00')

    def test_per_item_shipping_is_multiplied(self):
        listing = self._listing(quantity=5, shipping_mode='per_item')

        self._buy(listing, 'buyer_a', 3)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(str(invoice.shipping_fee), '15.00')
        self.assertEqual(invoice.total, Decimal('36.00') + Decimal('15.00'))

    def test_each_buyer_gets_their_own_invoice(self):
        listing = self._listing(quantity=5)

        self._buy(listing, 'buyer_a', 1)
        self._buy(listing, 'buyer_b', 2)

        invoices = Invoice.objects.filter(listing=listing).order_by('pk')
        self.assertEqual([i.quantity for i in invoices], [1, 2])
        self.assertEqual(
            [str(i.amount) for i in invoices], ['12.00', '24.00']
        )
        self.assertEqual(len({i.buyer_id for i in invoices}), 2)

    # ── Emails ──────────────────────────────────────────────────────────────

    def test_purchase_emails_state_the_quantity(self):
        listing = self._listing(quantity=5)
        mail.outbox = []

        self._buy(listing, 'buyer_a', 3)

        bodies = '\n'.join(m.body for m in mail.outbox)
        self.assertIn('Quantity:', bodies)
        self.assertIn('3', bodies)
        self.assertIn('36.00', bodies)

    # ── Listing page ────────────────────────────────────────────────────────

    def test_detail_page_shows_stock_and_a_quantity_input(self):
        listing = self._listing(quantity=4)
        client = Client()
        client.force_login(self._buyer('viewer'))

        response = client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )
        html = response.content.decode()

        self.assertContains(response, '4 available')
        self.assertIn('name="quantity"', html)
        self.assertIn('max="4"', html)

    def test_detail_page_of_a_sold_out_listing_offers_no_form(self):
        """A stale page must not be able to submit a purchase."""
        listing = self._listing(quantity=1)
        self._buy(listing, 'buyer_a', 1)

        client = Client()
        client.force_login(self._buyer('latecomer'))
        response = client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertNotIn('name="quantity"', response.content.decode())
        self.assertContains(response, 'sold out')


class _StockBlindBuyNowForm(BuyNowForm):
    """A BuyNowForm that skips its stock check.

    Stands in for the real race: a form that validated while stock was still
    available, by the time the write happens it is gone. With the form's own
    check removed, the only thing left to stop an oversell is the conditional
    UPDATE in the view — which is exactly what these tests are pinning down.
    """

    def clean_quantity(self):
        return self.cleaned_data['quantity']


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowOversellGuardTests(TestCase):
    """
    The stock decrement must be safe without help from the form.

    Deterministic, and it runs on every backend — unlike the threaded tests
    below, which SQLite cannot host at all.
    """

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('guardseller', first_name='Guard', last_name='Seller')

    def _listing(self, quantity):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Guarded Daylily',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=quantity,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _buy(self, listing, username, quantity):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = 'US'
        user.profile.save(update_fields=['country'])
        client = Client()
        client.force_login(user)
        return client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': quantity}
        )

    @patch('auctions.views.BuyNowForm', _StockBlindBuyNowForm)
    def test_oversell_is_refused_even_if_the_form_does_not_catch_it(self):
        listing = self._listing(3)
        self._buy(listing, 'guard_a', 2)

        self._buy(listing, 'guard_b', 2)

        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 1)
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 1)

    @patch('auctions.views.BuyNowForm', _StockBlindBuyNowForm)
    def test_stock_never_goes_negative(self):
        listing = self._listing(1)
        self._buy(listing, 'guard_c', 1)

        self._buy(listing, 'guard_d', 5)

        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 0)
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 1)

    @patch('auctions.views.BuyNowForm', _StockBlindBuyNowForm)
    def test_exact_remaining_still_succeeds(self):
        """The guard must not be so strict that a legitimate purchase fails."""
        listing = self._listing(4)
        self._buy(listing, 'guard_e', 1)

        self._buy(listing, 'guard_f', 3)

        listing.refresh_from_db()
        self.assertEqual(listing.quantity_remaining, 0)
        self.assertTrue(listing.is_closed)
        self.assertEqual(Invoice.objects.filter(listing=listing).count(), 2)


@skipUnless(
    connection.features.has_select_for_update,
    'Needs a backend with row locking and concurrent writers; SQLite answers '
    '"database table is locked" as soon as two threads write, and reports '
    'has_select_for_update = False so select_for_update() is a no-op there.',
)
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowRaceConditionTests(TransactionTestCase):
    """
    Genuinely concurrent purchases must never oversell.

    TransactionTestCase rather than TestCase: TestCase wraps everything in one
    transaction that never commits, so concurrent threads would not see each
    other's writes at all.
    """

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('raceseller', first_name='Race', last_name='Seller')
        now = timezone.now()
        self.listing = AuctionListing.objects.create(
            title='Contested Daylily',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=2,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _buyer(self, username):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = 'US'
        user.profile.save(update_fields=['country'])
        return user

    def test_concurrent_purchases_never_oversell(self):
        """Two units left; A asks for 2 and B for 1, at the same moment."""
        buyer_a = self._buyer('racer_a')
        buyer_b = self._buyer('racer_b')
        url = reverse('buy_now', kwargs={'pk': self.listing.pk})
        start = threading.Barrier(2)

        errors = []

        def purchase(user, quantity):
            try:
                client = Client()
                client.force_login(user)
                # Bounded so a thread that dies early cannot hang the suite on
                # a barrier that will never be reached.
                start.wait(timeout=20)
                client.post(url, {'quantity': quantity})
            except Exception as exc:  # noqa: BLE001 - surfaced via assert below
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=purchase, args=(buyer_a, 2)),
            threading.Thread(target=purchase, args=(buyer_b, 1)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [], f'purchase threads raised: {errors}')
        self.listing.refresh_from_db()
        sold = sum(
            Invoice.objects.filter(listing=self.listing).values_list(
                'quantity', flat=True
            )
        )

        # Whichever order they landed in, exactly one can succeed here: 2 + 1
        # exceeds the 2 in stock, and a partial sale is never made.
        self.assertLessEqual(sold, 2)
        self.assertEqual(sold + self.listing.quantity_remaining, 2)
        self.assertGreaterEqual(sold, 1, 'at least one purchase should succeed')

    def test_concurrent_purchases_that_fit_both_succeed(self):
        """Two units, one each — both should go through."""
        buyer_a = self._buyer('fits_a')
        buyer_b = self._buyer('fits_b')
        url = reverse('buy_now', kwargs={'pk': self.listing.pk})
        start = threading.Barrier(2)

        errors = []

        def purchase(user):
            try:
                client = Client()
                client.force_login(user)
                start.wait(timeout=20)
                client.post(url, {'quantity': 1})
            except Exception as exc:  # noqa: BLE001 - surfaced via assert below
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=purchase, args=(buyer_a,)),
            threading.Thread(target=purchase, args=(buyer_b,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [], f'purchase threads raised: {errors}')
        self.listing.refresh_from_db()
        sold = sum(
            Invoice.objects.filter(listing=self.listing).values_list(
                'quantity', flat=True
            )
        )
        self.assertEqual(sold, 2)
        self.assertEqual(self.listing.quantity_remaining, 0)
        self.assertTrue(self.listing.is_closed)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowStockPolicyTests(TestCase):
    """Stock caps availability; the end time is what closes a listing."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('policyseller', first_name='Policy', last_name='Seller')

    def test_ended_listing_with_stock_left_is_closed_by_the_scheduled_job(self):
        now = timezone.now()
        listing = AuctionListing.objects.create(
            title='Unsold Stock',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=5,
            starts_at=now - timedelta(days=8),
            ends_at=now - timedelta(minutes=1),
            is_active=True,
        )

        call_command('close_ended_auctions', verbosity=0)

        listing.refresh_from_db()
        self.assertTrue(listing.is_closed)
        self.assertFalse(listing.is_active)
        # Closing is not a sale: leftover stock stays on the record, and no
        # invoice is invented for units nobody bought.
        self.assertEqual(listing.quantity_remaining, 5)
        self.assertFalse(Invoice.objects.filter(listing=listing).exists())

    def test_auction_listings_are_untouched_by_stock_fields(self):
        now = timezone.now()
        listing = AuctionListing.objects.create(
            title='Plain Auction',
            category=self.category,
            seller=self.seller,
            start_price='5.00',
            listing_type='auction',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            is_active=True,
        )

        self.assertIsNone(listing.quantity_available)
        self.assertIsNone(listing.quantity_remaining)
        self.assertEqual(listing.units_remaining, 0)
        self.assertFalse(listing.is_sold_out)

    def test_buy_now_validation_requires_a_quantity(self):
        now = timezone.now()
        listing = AuctionListing(
            title='No Quantity',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        with self.assertRaises(ValidationError) as ctx:
            listing.full_clean()
        self.assertIn('quantity_available', ctx.exception.message_dict)

    def test_programmatic_creation_without_quantity_defaults_to_one(self):
        """save() must not leave a buy_now listing silently unbuyable."""
        now = timezone.now()
        listing = AuctionListing.objects.create(
            title='Implicit Single',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )

        self.assertEqual(listing.quantity_available, 1)
        self.assertEqual(listing.quantity_remaining, 1)

    def test_shipping_for_respects_the_mode(self):
        listing = AuctionListing(
            listing_type='buy_now', seller=self.seller, shipping_mode='flat'
        )
        self.assertEqual(listing.shipping_for(4), Decimal('5.00'))

        listing.shipping_mode = 'per_item'
        self.assertEqual(listing.shipping_for(4), Decimal('20.00'))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BuyNowQuantityDefaultTests(TestCase):
    """A POST with no quantity still buys one, as this endpoint always did."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('defaultseller', first_name='Default', last_name='Seller')
        now = timezone.now()
        self.listing = AuctionListing.objects.create(
            title='Default Qty Daylily',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=4,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )

    def _client(self, username):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = 'US'
        user.profile.save(update_fields=['country'])
        client = Client()
        client.force_login(user)
        return client

    def test_missing_quantity_buys_one(self):
        client = self._client('legacy_poster')

        client.post(reverse('buy_now', kwargs={'pk': self.listing.pk}))

        invoice = Invoice.objects.get(listing=self.listing)
        self.assertEqual(invoice.quantity, 1)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity_remaining, 3)

    def test_blank_quantity_buys_one(self):
        client = self._client('blank_poster')

        client.post(
            reverse('buy_now', kwargs={'pk': self.listing.pk}), {'quantity': ''}
        )

        self.assertEqual(Invoice.objects.get(listing=self.listing).quantity, 1)

    def test_non_numeric_quantity_is_refused(self):
        """An absent value defaults; a nonsense value must not."""
        client = self._client('junk_poster')

        client.post(
            reverse('buy_now', kwargs={'pk': self.listing.pk}),
            {'quantity': 'lots'},
        )

        self.assertFalse(Invoice.objects.filter(listing=self.listing).exists())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity_remaining, 4)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CloseEndedBuyNowTests(TestCase):
    """The scheduled close job has to report and notify correctly for buy_now."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller(
            'closingseller', first_name='Closing', last_name='Seller',
            email='seller@example.com',
        )

    def _ended_buy_now(self, quantity=3):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='Ended Buy Now',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=quantity,
            starts_at=now - timedelta(days=8),
            ends_at=now - timedelta(minutes=1),
            is_active=True,
        )

    def test_close_is_reported_not_silently_skipped(self):
        """A buy_now listing closes with no winner; that is still a close."""
        listing = self._ended_buy_now()
        out = StringIO()

        call_command('close_ended_auctions', stdout=out)

        listing.refresh_from_db()
        self.assertTrue(listing.is_closed)
        self.assertIn('closed=1', out.getvalue())

    def test_no_bids_email_is_not_sent_for_buy_now(self):
        """"No bids" is auction wording and may be flatly untrue here."""
        self._ended_buy_now()
        mail.outbox = []

        call_command('close_ended_auctions', verbosity=0)

        self.assertEqual(mail.outbox, [])

    def test_no_bid_auction_still_reports_and_notifies(self):
        """The counter fix must not change auction behaviour."""
        now = timezone.now()
        AuctionListing.objects.create(
            title='Nobody Bid',
            category=self.category,
            seller=self.seller,
            start_price='5.00',
            listing_type='auction',
            starts_at=now - timedelta(days=8),
            ends_at=now - timedelta(minutes=1),
            is_active=True,
        )
        out = StringIO()

        with override_settings(ADMIN_EMAIL='admin@example.com'):
            call_command('close_ended_auctions', stdout=out)

        self.assertIn('closed=1', out.getvalue())
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MultiBuyDiscountTests(TestCase):
    """
    A per-listing discount on every unit beyond the first.

    Set by the admin per listing, not a site-wide formula: full price for the
    first plant, ``buy_now_price - additional_item_discount`` for each after.
    """

    def setUp(self):
        cache.clear()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('discountseller', first_name='Discount')
        self.buyer = User.objects.create_user('dbuyer', 'dbuyer@example.com', 'pw')
        Subscription.objects.create(
            user=self.buyer, plan='monthly', status='active'
        )
        self.buyer.profile.country = 'US'
        self.buyer.profile.save(update_fields=['country'])
        mail.outbox = []

    def _listing(self, price='10.00', discount='3.00', quantity=5, **kwargs):
        now = timezone.now()
        defaults = dict(
            title='Discounted Daylily',
            category=self.category,
            seller=self.seller,
            start_price=price,
            listing_type='buy_now',
            buy_now_price=price,
            additional_item_discount=discount,
            quantity_available=quantity,
            shipping_mode='flat',
            shipping_fee='5.00',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )
        defaults.update(kwargs)
        listing = AuctionListing.objects.create(**defaults)
        listing.refresh_from_db()
        return listing

    # ── The arithmetic ───────────────────────────────────────────────────────

    def test_three_of_a_ten_dollar_plant_with_three_off_costs_twenty_four(self):
        """The worked example from the spec: $10 + $7 + $7."""
        listing = self._listing(price='10.00', discount='3.00')

        self.assertEqual(listing.price_for(3), Decimal('24.00'))

    def test_one_unit_pays_full_price(self):
        listing = self._listing(price='10.00', discount='3.00')

        self.assertEqual(listing.price_for(1), Decimal('10.00'))

    def test_two_units_discount_only_the_second(self):
        listing = self._listing(price='10.00', discount='3.00')

        self.assertEqual(listing.price_for(2), Decimal('17.00'))

    def test_no_discount_is_plain_multiplication(self):
        listing = self._listing(price='10.00', discount='0')

        self.assertEqual(listing.price_for(4), Decimal('40.00'))
        self.assertFalse(listing.has_quantity_discount)

    def test_the_additional_unit_price_is_exposed_for_display(self):
        listing = self._listing(price='12.00', discount='3.00')

        self.assertEqual(listing.additional_unit_price, Decimal('9.00'))
        self.assertTrue(listing.has_quantity_discount)

    def test_a_discount_equal_to_the_price_makes_extras_free(self):
        listing = self._listing(price='10.00', discount='10.00')

        self.assertEqual(listing.price_for(3), Decimal('10.00'))

    def test_extra_units_never_price_below_zero(self):
        """
        clean() refuses an over-large discount, but a bulk update bypasses it.

        Flooring here means a listing written that way undercharges rather than
        paying the buyer to take plants away.
        """
        listing = self._listing(price='10.00', discount='0')
        AuctionListing.objects.filter(pk=listing.pk).update(
            additional_item_discount='25.00'
        )
        listing.refresh_from_db()

        self.assertEqual(listing.additional_unit_price, Decimal('0.00'))
        self.assertEqual(listing.price_for(3), Decimal('10.00'))

    def test_price_for_zero_or_negative_quantity_is_zero(self):
        listing = self._listing()

        self.assertEqual(listing.price_for(0), Decimal('0.00'))

    # ── Model validation ─────────────────────────────────────────────────────

    def test_a_discount_larger_than_the_price_is_rejected(self):
        listing = self._listing(price='10.00', discount='0')
        listing.additional_item_discount = Decimal('12.00')

        with self.assertRaises(ValidationError) as caught:
            listing.full_clean()

        self.assertIn('additional_item_discount', caught.exception.error_dict)

    def test_a_negative_discount_is_rejected(self):
        listing = self._listing(price='10.00', discount='0')
        listing.additional_item_discount = Decimal('-1.00')

        with self.assertRaises(ValidationError) as caught:
            listing.full_clean()

        self.assertIn('additional_item_discount', caught.exception.error_dict)

    def test_a_discount_equal_to_the_price_is_allowed(self):
        """Free extras is a real offer; it is only *below* zero that is not."""
        listing = self._listing(price='10.00', discount='10.00')

        listing.full_clean()

    def test_an_auction_listing_ignores_the_discount_entirely(self):
        now = timezone.now()
        listing = AuctionListing.objects.create(
            title='Auction With Junk Discount',
            category=self.category, seller=self.seller,
            start_price='10.00', listing_type='auction',
            additional_item_discount='99.00',
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=3),
        )

        listing.full_clean()
        self.assertFalse(listing.has_quantity_discount)

    # ── At purchase ──────────────────────────────────────────────────────────

    def test_buying_three_records_the_discounted_amount(self):
        listing = self._listing(price='10.00', discount='3.00')
        self.client.force_login(self.buyer)

        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 3}
        )

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(invoice.quantity, 3)
        self.assertEqual(invoice.amount, Decimal('24.00'))
        # Shipping is flat, so the total is the item total plus one fee.
        self.assertEqual(invoice.total, Decimal('29.00'))

    def test_the_discount_reaches_a_combined_invoice_already_applied(self):
        """
        It is applied at purchase, so invoicing needs no knowledge of it.

        The line item's amount is what the buyer agreed to; the combined
        invoice just adds the lines up.
        """
        from auctions.services import generate_combined_invoices

        listing = self._listing(price='10.00', discount='3.00')
        self.client.force_login(self.buyer)
        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 3}
        )

        draft = generate_combined_invoices()[0]

        self.assertEqual(draft.subtotal, Decimal('24.00'))

    def test_the_seller_notice_spells_out_both_tiers(self):
        listing = self._listing(price='10.00', discount='3.00')
        self.client.force_login(self.buyer)

        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 3}
        )

        seller_mail = [m for m in mail.outbox if self.seller.email in m.to]
        self.assertEqual(len(seller_mail), 1)
        self.assertIn('$10.00 for the first', seller_mail[0].body)
        self.assertIn('$7.00 each after', seller_mail[0].body)

    def test_an_undiscounted_sale_says_each(self):
        listing = self._listing(price='10.00', discount='0')
        self.client.force_login(self.buyer)

        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 2}
        )

        seller_mail = [m for m in mail.outbox if self.seller.email in m.to]
        self.assertIn('$10.00 each', seller_mail[0].body)

    # ── On the listing page ──────────────────────────────────────────────────

    def test_the_listing_page_names_both_prices(self):
        listing = self._listing(price='12.00', discount='3.00')
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertContains(response, '$12.00')
        self.assertContains(response, '$9.00 each after that')
        self.assertContains(response, 'save $3.00 per extra plant')

    def test_an_undiscounted_listing_page_says_each_and_nothing_more(self):
        listing = self._listing(price='12.00', discount='0')
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertNotContains(response, 'each after that')
        self.assertNotContains(response, 'per extra plant')

    def test_the_page_publishes_both_prices_for_the_running_total(self):
        """The JS total must not recompute the discount from scratch."""
        listing = self._listing(price='12.00', discount='3.00')
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertContains(response, 'buy-now-unit-price')
        self.assertContains(response, 'buy-now-additional-price')
        self.assertContains(response, 'buy-now-shipping-rate')

    # ── Through the Add Listing form ─────────────────────────────────────────

    def test_the_add_listing_form_saves_a_discount(self):
        staff = User.objects.create_user(
            'discountboss', 'discountboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(staff)
        now = timezone.now()

        self.client.post(
            reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk}),
            {
                'title': 'Form Discounted',
                'category': self.category.pk,
                'listing_type': 'buy_now',
                'buy_now_price': '20.00',
                'quantity_available': '4',
                'additional_item_discount': '5.00',
                'shipping_mode': 'flat',
                'shipping_fee': '6.00',
                'starts_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': (now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        listing = AuctionListing.objects.get(title='Form Discounted')
        self.assertEqual(listing.additional_item_discount, Decimal('5.00'))
        self.assertEqual(listing.price_for(2), Decimal('35.00'))

    def test_the_form_treats_a_blank_discount_as_none(self):
        staff = User.objects.create_user(
            'blankboss', 'blankboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(staff)
        now = timezone.now()

        self.client.post(
            reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk}),
            {
                'title': 'No Discount Given',
                'category': self.category.pk,
                'listing_type': 'buy_now',
                'buy_now_price': '20.00',
                'quantity_available': '4',
                'additional_item_discount': '',
                'shipping_mode': 'flat',
                'starts_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': (now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        listing = AuctionListing.objects.get(title='No Discount Given')
        self.assertEqual(listing.additional_item_discount, Decimal('0'))

    def test_the_form_refuses_a_discount_above_the_price(self):
        staff = User.objects.create_user(
            'badboss', 'badboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(staff)
        now = timezone.now()

        response = self.client.post(
            reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk}),
            {
                'title': 'Too Much Off',
                'category': self.category.pk,
                'listing_type': 'buy_now',
                'buy_now_price': '10.00',
                'quantity_available': '4',
                'additional_item_discount': '15.00',
                'shipping_mode': 'flat',
                'starts_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': (now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot be more than the price')
        self.assertFalse(AuctionListing.objects.filter(title='Too Much Off').exists())

    def test_switching_to_auction_clears_a_typed_discount(self):
        """The hidden Buy It Now inputs still post their values."""
        staff = User.objects.create_user(
            'switchboss', 'switchboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(staff)
        now = timezone.now()

        self.client.post(
            reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk}),
            {
                'title': 'Actually An Auction',
                'category': self.category.pk,
                'listing_type': 'auction',
                'start_price': '10.00',
                'additional_item_discount': '4.00',
                'starts_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': (now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        listing = AuctionListing.objects.get(title='Actually An Auction')
        self.assertEqual(listing.additional_item_discount, Decimal('0'))

    # ── Copying ──────────────────────────────────────────────────────────────

    def test_a_copied_listing_keeps_the_discount(self):
        from auctions.services import copy_listing

        source = self._listing(price='10.00', discount='3.00')

        copy = copy_listing(source)

        self.assertEqual(copy.additional_item_discount, Decimal('3.00'))
