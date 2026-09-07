"""Tests for the weekly setup flow: one listing at a time, plus a read-only list.

The bulk formset this replaced could never actually save: it collected no
category and the view set none, so every submission hit NOT NULL on
auctions_auctionlisting.category_id. Step 1 was broken too — the seller form
passed a `category` kwarg that migration 0005 had removed. Step 1 now only
selects an existing seller account; there is nothing to create there any more.
"""

import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing
from auctions.tests.utils import make_seller

User = get_user_model()


def listing_payload(**overrides):
    now = timezone.now()
    data = {
        'title': 'Stella de Oro',
        'description': 'A reliable rebloomer.',
        'listing_type': 'auction',
        'start_price': '10.00',
        'starts_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
        'ends_at': (now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
    }
    data.update(overrides)
    return data


class WeeklySetupTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'setupboss', 'setupboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(self.staff)
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller(
            'sunnygardens', first_name='Sunny', last_name='Gardens',
            active_week=timezone.now().date(),
        )
        self.url = reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk})

    def payload(self, **overrides):
        return listing_payload(category=self.category.pk, **overrides)


class SellerStepTests(WeeklySetupTestCase):
    """Step 1 selects a seller account. It no longer creates anything."""

    def test_selecting_a_seller_succeeds(self):
        response = self.client.post(
            reverse('weekly_setup'), {'seller': self.seller.pk}
        )

        self.assertRedirects(response, self.url)

    def test_the_picker_offers_seller_flagged_accounts(self):
        response = self.client.get(reverse('weekly_setup'))

        self.assertTrue(response.context['has_sellers'])
        self.assertContains(response, 'Sunny Gardens')

    def test_a_non_seller_account_cannot_be_chosen(self):
        """The picker is the gate: any User pk would otherwise be accepted."""
        buyer = User.objects.create_user('plainbuyer', 'plain@example.com', 'pw')

        response = self.client.post(reverse('weekly_setup'), {'seller': buyer.pk})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AuctionListing.objects.filter(seller=buyer).exists())

    def test_choosing_nothing_is_an_error_not_a_crash(self):
        response = self.client.post(reverse('weekly_setup'), {})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_no_sellers_yet_explains_what_to_do(self):
        """An empty dropdown is a dead end; the fix lives in the admin."""
        from auctions.models import UserProfile

        UserProfile.objects.filter(user=self.seller).update(is_seller=False)

        response = self.client.get(reverse('weekly_setup'))

        self.assertFalse(response.context['has_sellers'])
        self.assertContains(response, 'No sellers found')
        self.assertContains(response, 'before adding listings')


class SingleListingCreationTests(WeeklySetupTestCase):
    def test_staff_only(self):
        self.client.logout()
        member = User.objects.create_user('member', 'member@example.com', 'pw')
        self.client.force_login(member)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_form_renders_one_listing_at_a_time(self):
        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weekly_setup/add_listing.html')
        # A single unprefixed form, not a formset.
        self.assertIn('name="title"', html)
        self.assertNotIn('listings-TOTAL_FORMS', html)
        self.assertNotIn('listings-0-title', html)

    def test_submitting_creates_exactly_one_listing(self):
        self.client.post(self.url, self.payload())

        self.assertEqual(AuctionListing.objects.count(), 1)
        listing = AuctionListing.objects.get()
        self.assertEqual(listing.title, 'Stella de Oro')
        self.assertEqual(listing.seller, self.seller)
        self.assertEqual(listing.category, self.category)
        self.assertTrue(listing.is_active)
        self.assertFalse(listing.is_closed)

    def test_success_redirects_back_to_the_same_page(self):
        response = self.client.post(self.url, self.payload())

        self.assertRedirects(response, self.url)

    def test_success_message_invites_another_entry(self):
        response = self.client.post(self.url, self.payload(), follow=True)

        self.assertContains(response, 'Stella de Oro')
        self.assertContains(response, 'Add another below')

    def test_form_is_empty_again_after_a_save(self):
        """The next entry starts blank — no leftover title in the input."""
        self.client.post(self.url, self.payload())

        response = self.client.get(self.url)
        form = response.context['form']

        self.assertIsNone(form['title'].value())
        self.assertFalse(form.is_bound)

    def test_dates_and_category_carry_over_to_the_next_entry(self):
        """Repeating them by hand for every plant is what made bulk feel needed."""
        self.client.post(self.url, self.payload())

        response = self.client.get(self.url)
        initial = response.context['form'].initial

        self.assertEqual(initial['category'], self.category.pk)
        self.assertIn('starts_at', initial)
        self.assertIn('ends_at', initial)

    def test_back_to_back_entries_both_save(self):
        self.client.post(self.url, self.payload(title='First'))
        self.client.post(self.url, self.payload(title='Second'))

        self.assertEqual(AuctionListing.objects.count(), 2)

    def test_buy_now_listing_saves_with_stock_and_shipping_mode(self):
        self.client.post(self.url, self.payload(
            title='Ruby Spider',
            listing_type='buy_now',
            start_price='',
            buy_now_price='12.00',
            quantity_available='4',
            shipping_mode='per_item',
        ))

        listing = AuctionListing.objects.get(title='Ruby Spider')
        self.assertEqual(listing.listing_type, 'buy_now')
        self.assertEqual(str(listing.buy_now_price), '12.00')
        self.assertEqual(listing.quantity_available, 4)
        self.assertEqual(listing.quantity_remaining, 4)
        self.assertEqual(listing.shipping_mode, 'per_item')

    def test_buy_now_requires_price_and_quantity(self):
        response = self.client.post(self.url, self.payload(
            listing_type='buy_now', start_price='', buy_now_price='', quantity_available='',
        ))

        self.assertEqual(AuctionListing.objects.count(), 0)
        self.assertContains(response, 'Required for a Buy It Now listing')
        self.assertContains(response, 'how many units are available')

    def test_auction_requires_a_start_price(self):
        response = self.client.post(self.url, self.payload(start_price=''))

        self.assertEqual(AuctionListing.objects.count(), 0)
        self.assertContains(response, 'Required for an auction listing')

    def test_category_is_required(self):
        """The bulk form never collected one, which is why saving always failed."""
        data = self.payload()
        data['category'] = ''

        response = self.client.post(self.url, data)

        self.assertEqual(AuctionListing.objects.count(), 0)
        self.assertEqual(response.status_code, 200)

    def test_end_must_be_after_start(self):
        now = timezone.now()
        response = self.client.post(self.url, self.payload(
            starts_at=(now + timedelta(days=8)).strftime('%Y-%m-%dT%H:%M'),
            ends_at=(now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
        ))

        self.assertEqual(AuctionListing.objects.count(), 0)
        self.assertContains(response, 'must be after the start time')

    def test_switching_to_auction_clears_stale_buy_now_values(self):
        """The hidden Buy It Now inputs still post their values."""
        self.client.post(self.url, self.payload(
            listing_type='auction',
            start_price='10.00',
            buy_now_price='99.00',
            quantity_available='7',
        ))

        listing = AuctionListing.objects.get()
        self.assertIsNone(listing.buy_now_price)
        self.assertIsNone(listing.quantity_available)

    def test_saved_datetimes_are_timezone_aware(self):
        self.client.post(self.url, self.payload())

        listing = AuctionListing.objects.get()
        self.assertIsNotNone(timezone.get_current_timezone().utcoffset(None) or True)
        self.assertTrue(timezone.is_aware(listing.starts_at))
        self.assertTrue(timezone.is_aware(listing.ends_at))


class InformationalListTests(WeeklySetupTestCase):
    """The list below the form is reference material, not a second editor."""

    def _list_section(self, response):
        """Just the read-only section, so the creation form is not in scope."""
        html = response.content.decode()
        match = re.search(
            r'<section id="existing-listings".*?</section>', html, re.S
        )
        self.assertIsNotNone(match, 'read-only listing section not found')
        return match.group(0)

    def test_new_listings_appear_after_the_redirect(self):
        response = self.client.post(self.url, self.payload(), follow=True)

        self.assertContains(response, 'Stella de Oro')
        self.assertIn('Stella de Oro', self._list_section(response))

    def test_most_recent_first(self):
        self.client.post(self.url, self.payload(title='Older'))
        self.client.post(self.url, self.payload(title='Newer'))

        section = self._list_section(self.client.get(self.url))

        self.assertLess(section.index('Newer'), section.index('Older'))

    def test_only_this_sellers_listings_are_shown(self):
        other = make_seller('otherseller', first_name='Other', last_name='Seller')
        now = timezone.now()
        AuctionListing.objects.create(
            title='Someone Elses Plant',
            category=self.category,
            seller=other,
            start_price='5.00',
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        self.client.post(self.url, self.payload(title='Mine'))

        section = self._list_section(self.client.get(self.url))

        self.assertIn('Mine', section)
        self.assertNotIn('Someone Elses Plant', section)

    def test_buy_now_rows_show_stock(self):
        self.client.post(self.url, self.payload(
            title='Ruby Spider', listing_type='buy_now', start_price='',
            buy_now_price='12.00', quantity_available='4', shipping_mode='flat',
        ))

        section = self._list_section(self.client.get(self.url))

        self.assertIn('Buy It Now', section)
        self.assertIn('4 of 4', section)

    def test_list_has_no_form_controls(self):
        self.client.post(self.url, self.payload())

        section = self._list_section(self.client.get(self.url))

        self.assertNotIn('<form', section)
        self.assertNotIn('<input', section)
        self.assertNotIn('<select', section)
        self.assertNotIn('<textarea', section)
        self.assertNotIn('<button', section)
        self.assertNotIn('csrfmiddlewaretoken', section)

    def test_list_offers_no_delete_or_inline_edit(self):
        self.client.post(self.url, self.payload())

        section = self._list_section(self.client.get(self.url))

        self.assertNotIn('delete', section.lower())
        self.assertNotIn('type="checkbox"', section)
        # The one link per row is a read-only hop to the admin.
        self.assertIn('View in admin', section)

    def test_empty_state_when_nothing_added_yet(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'Nothing added yet')


class BulkFormsetRemovedTests(TestCase):
    """The bulk entry path must be gone, not merely unlinked."""

    def test_formset_no_longer_exists(self):
        import auctions.weekly_setup_forms as forms_module

        self.assertFalse(hasattr(forms_module, 'WeeklyListingFormSet'))
        self.assertFalse(hasattr(forms_module, 'WeeklyListingForm'))

    def test_bulk_template_is_gone(self):
        from django.template import TemplateDoesNotExist
        from django.template.loader import get_template

        with self.assertRaises(TemplateDoesNotExist):
            get_template('weekly_setup/listings.html')


class ListingShippingFeeTests(WeeklySetupTestCase):
    """Buy It Now listings can price their own shipping.

    Shipping used to live only on the Seller, so the add-listing page offered a
    mode (flat vs per item) with no amount anywhere — nothing to price a plant
    that ships differently from the rest of the seller's stock.
    """

    def test_form_offers_a_shipping_cost_field(self):
        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertIn('name="shipping_fee"', html)
        self.assertIn('Shipping cost', html)

    def test_it_defaults_to_the_sellers_standard_fee(self):
        response = self.client.get(self.url)

        # Decimal from the database, vs the string this fixture assigned.
        self.assertEqual(
            Decimal(str(response.context['form'].initial['shipping_fee'])),
            self.seller.profile.seller_shipping_fee,
        )

    def test_help_text_names_the_sellers_fallback(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'Sunny Gardens')
        self.assertContains(response, '5.00')

    def test_a_per_listing_fee_is_saved(self):
        self.client.post(self.url, self.payload(
            title='Heavy Fan', listing_type='buy_now', start_price='',
            buy_now_price='20.00', quantity_available='3',
            shipping_mode='flat', shipping_fee='9.50',
        ))

        listing = AuctionListing.objects.get(title='Heavy Fan')
        self.assertEqual(str(listing.shipping_fee), '9.50')
        self.assertEqual(listing.shipping_rate, Decimal('9.50'))

    def test_blank_falls_back_to_the_seller_fee(self):
        self.client.post(self.url, self.payload(
            title='Standard Fan', listing_type='buy_now', start_price='',
            buy_now_price='20.00', quantity_available='3',
            shipping_mode='flat', shipping_fee='',
        ))

        listing = AuctionListing.objects.get(title='Standard Fan')
        self.assertIsNone(listing.shipping_fee)
        self.assertEqual(listing.shipping_rate, Decimal('5.00'))

    def test_flat_fee_is_charged_once(self):
        self.client.post(self.url, self.payload(
            title='Flat Ship', listing_type='buy_now', start_price='',
            buy_now_price='20.00', quantity_available='5',
            shipping_mode='flat', shipping_fee='9.50',
        ))

        listing = AuctionListing.objects.get(title='Flat Ship')
        self.assertEqual(listing.shipping_for(4), Decimal('9.50'))

    def test_per_item_fee_is_multiplied(self):
        self.client.post(self.url, self.payload(
            title='Per Item Ship', listing_type='buy_now', start_price='',
            buy_now_price='20.00', quantity_available='5',
            shipping_mode='per_item', shipping_fee='3.00',
        ))

        listing = AuctionListing.objects.get(title='Per Item Ship')
        self.assertEqual(listing.shipping_for(4), Decimal('12.00'))

    def test_auction_listings_do_not_keep_a_listing_fee(self):
        """The input is hidden for auctions but still posts its prefilled value."""
        self.client.post(self.url, self.payload(
            title='Just An Auction', shipping_fee='9.50',
        ))

        listing = AuctionListing.objects.get(title='Just An Auction')
        self.assertIsNone(listing.shipping_fee)
        # Auctions settle from the seller's fee when they close.
        self.assertEqual(listing.shipping_rate, Decimal('5.00'))

    def test_changing_the_seller_fee_does_not_move_a_priced_listing(self):
        self.client.post(self.url, self.payload(
            title='Fixed Ship', listing_type='buy_now', start_price='',
            buy_now_price='20.00', quantity_available='2',
            shipping_mode='flat', shipping_fee='9.50',
        ))

        profile = self.seller.profile
        profile.seller_shipping_fee = '99.00'
        profile.save(update_fields=['seller_shipping_fee'])

        listing = AuctionListing.objects.get(title='Fixed Ship')
        self.assertEqual(listing.shipping_rate, Decimal('9.50'))


class ShippingFeeAtPurchaseTests(TestCase):
    """The fee a buyer is charged comes from the listing, end to end."""

    def setUp(self):
        from auctions.models import Subscription

        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('shipseller', first_name='Ship', last_name='Seller')
        self.buyer = User.objects.create_user('shipbuyer', 'shipbuyer@example.com', 'pw')
        Subscription.objects.create(user=self.buyer, plan='monthly', status='active')
        self.buyer.profile.country = 'US'
        self.buyer.profile.save(update_fields=['country'])
        self.client.force_login(self.buyer)

    def _listing(self, **kwargs):
        now = timezone.now()
        defaults = dict(
            title='Shippable',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type='buy_now',
            buy_now_price='10.00',
            quantity_available=5,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )
        defaults.update(kwargs)
        return AuctionListing.objects.create(**defaults)

    def _buy(self, listing, quantity):
        return self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': quantity}
        )

    def test_invoice_uses_the_listing_fee(self):
        from auctions.models import Invoice

        listing = self._listing(shipping_fee='9.50', shipping_mode='flat')

        self._buy(listing, 2)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(str(invoice.shipping_fee), '9.50')

    def test_invoice_multiplies_a_per_item_listing_fee(self):
        from auctions.models import Invoice

        listing = self._listing(shipping_fee='3.00', shipping_mode='per_item')

        self._buy(listing, 3)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(str(invoice.shipping_fee), '9.00')

    def test_invoice_falls_back_to_the_seller_fee(self):
        from auctions.models import Invoice

        listing = self._listing(shipping_fee=None, shipping_mode='flat')

        self._buy(listing, 2)

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(str(invoice.shipping_fee), '5.00')

    def test_listing_page_shows_the_shipping_amount(self):
        listing = self._listing(shipping_fee='9.50', shipping_mode='flat')

        response = self.client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertContains(response, '9.50')
