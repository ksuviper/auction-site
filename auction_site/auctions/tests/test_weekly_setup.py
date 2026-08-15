"""Tests for the weekly setup flow: one listing at a time, plus a read-only list.

The bulk formset this replaced could never actually save: it collected no
category and the view set none, so every submission hit NOT NULL on
auctions_auctionlisting.category_id. Creating a seller was broken too — the form
passed a `category` kwarg that migration 0005 removed from Seller. Both paths are
covered below so neither can regress silently.
"""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing, Seller

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
        self.seller = Seller.objects.create(
            name='Sunny Gardens',
            accepted_payment_methods='PayPal',
            shipping_fee='5.00',
            active_week=timezone.now().date(),
        )
        self.url = reverse('weekly_setup_listings', kwargs={'seller_pk': self.seller.pk})

    def payload(self, **overrides):
        return listing_payload(category=self.category.pk, **overrides)


class SellerStepTests(WeeklySetupTestCase):
    """Step 1 must still work — it is preserved, not replaced."""

    def test_creating_a_new_seller_succeeds(self):
        """Regression: this used to raise TypeError on Seller(category=...)."""
        response = self.client.post(
            reverse('weekly_setup'),
            {
                'seller_mode': 'new',
                'name': 'Fresh Seller',
                'accepted_payment_methods': 'Venmo',
                'shipping_fee': '7.50',
                'active_week': timezone.now().date().isoformat(),
            },
        )

        seller = Seller.objects.get(name='Fresh Seller')
        self.assertRedirects(
            response,
            reverse('weekly_setup_listings', kwargs={'seller_pk': seller.pk}),
        )

    def test_selecting_an_existing_seller_succeeds(self):
        response = self.client.post(
            reverse('weekly_setup'),
            {'seller_mode': 'existing', 'existing_seller': self.seller.pk},
        )

        self.assertRedirects(response, self.url)

    def test_new_seller_still_requires_its_own_fields(self):
        response = self.client.post(reverse('weekly_setup'), {'seller_mode': 'new'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Seller.objects.filter(name='').exists())


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
        other = Seller.objects.create(
            name='Other Seller', accepted_payment_methods='Cash', shipping_fee='1.00'
        )
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
