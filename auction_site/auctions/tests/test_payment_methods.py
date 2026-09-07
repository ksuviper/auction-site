"""Tests for a seller's payment details and where they are allowed to appear.

The fields are free text on purpose — Venmo, PayPal and Cash App have real
payment links, Zelle has only an email or phone, and Apple/Google Pay are not
visit-a-link methods at all — so nothing here validates them as URLs.

The visibility split is the part worth pinning: which methods a seller accepts
is public, because a buyer needs it before deciding to bid; the handles and
addresses appear only on an invoice, where the reader already owes that seller
money and the page is restricted to them.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing, Invoice, UserProfile
from auctions.tests.utils import make_seller
from auctions.utils import payment_block

User = get_user_model()

HANDLES = {
    'venmo_info': '@brynn-vale',
    'paypal_info': 'pay@brynnvale.example',
    'cashapp_info': '$brynnvale',
    'zelle_info': '555-0100',
}


class PaymentFixtureMixin:
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('brynn', first_name='Brynn', last_name='Vale')
        UserProfile.objects.filter(user=self.seller).update(
            seller_payment_methods='', apple_pay_info='', google_pay_info='',
            **HANDLES
        )
        self.seller.refresh_from_db()
        self.buyer = User.objects.create_user('cass', 'cass@example.com', 'pw')

    def _listing(self, **kwargs):
        now = timezone.now()
        defaults = dict(
            title='Brynn Plant', category=self.category, seller=self.seller,
            start_price='10.00', starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5), is_active=True,
        )
        defaults.update(kwargs)
        return AuctionListing.objects.create(**defaults)


class PaymentOptionsTests(PaymentFixtureMixin, TestCase):
    def test_it_lists_only_the_methods_filled_in(self):
        options = dict(self.seller.profile.payment_options)

        self.assertEqual(options['Venmo'], '@brynn-vale')
        self.assertEqual(options['Zelle'], '555-0100')
        self.assertNotIn('Apple Pay', options)
        self.assertNotIn('Google Pay', options)

    def test_a_seller_with_nothing_on_file_lists_nothing(self):
        bare = make_seller('bare', paypal_info='')

        self.assertEqual(bare.profile.payment_options, [])
        self.assertEqual(bare.profile.payment_method_names, [])

    def test_whitespace_alone_does_not_count_as_a_method(self):
        UserProfile.objects.filter(user=self.seller).update(apple_pay_info='   ')
        self.seller.refresh_from_db()

        self.assertNotIn('Apple Pay', self.seller.profile.payment_method_names)

    def test_the_free_text_note_comes_last_as_other(self):
        UserProfile.objects.filter(user=self.seller).update(
            seller_payment_methods='Cheque by post, ask for the address'
        )
        self.seller.refresh_from_db()

        options = self.seller.profile.payment_options

        self.assertEqual(options[-1][0], 'Other')
        self.assertIn('Cheque', options[-1][1])

    def test_method_names_never_include_the_handles(self):
        names = self.seller.profile.payment_method_names

        self.assertIn('Venmo', names)
        for handle in HANDLES.values():
            self.assertNotIn(handle, ' '.join(names))

    def test_the_email_block_lists_each_method_on_its_own_line(self):
        block = payment_block(self.seller.profile)

        self.assertIn('Venmo: @brynn-vale', block)
        self.assertIn('PayPal: pay@brynnvale.example', block)
        self.assertEqual(len(block.splitlines()), 4)

    def test_the_email_block_says_so_when_there_is_nothing_on_file(self):
        self.assertEqual(payment_block(make_seller('empty', paypal_info='').profile), '(ask the seller)')

    def test_the_email_block_tolerates_a_missing_profile(self):
        self.assertEqual(payment_block(None), '(ask the seller)')


class PublicVisibilityTests(PaymentFixtureMixin, TestCase):
    """Method names are public; the handles are not."""

    def test_the_seller_page_names_the_methods(self):
        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': self.seller.pk})
        )

        self.assertContains(response, 'Venmo')
        self.assertContains(response, 'Zelle')

    def test_the_seller_page_does_not_leak_the_handles(self):
        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': self.seller.pk})
        )

        for handle in HANDLES.values():
            self.assertNotContains(response, handle)

    def test_the_listing_page_does_not_leak_the_handles(self):
        listing = self._listing()

        response = self.client.get(
            reverse('listing_detail', kwargs={'pk': listing.pk})
        )

        self.assertContains(response, 'Venmo')
        for handle in HANDLES.values():
            self.assertNotContains(response, handle)


class InvoiceVisibilityTests(PaymentFixtureMixin, TestCase):
    """
    The buyer named on a *billed* invoice — and only they — get the details.

    Being billed is the gate as well as being the right person: combined
    invoicing means a sale sits unbilled until an admin sends the invoice
    covering it, and until then there is nothing to pay.
    """

    def setUp(self):
        super().setUp()
        self.invoice = Invoice.objects.create(
            listing=self._listing(), buyer=self.buyer, seller=self.seller,
            amount='20.00', shipping_fee='5.00',
        )
        self.url = reverse('invoice_detail', kwargs={'pk': self.invoice.pk})
        self._bill()

    def _bill(self):
        """Put the sale onto a combined invoice and send it."""
        from auctions.services import (
            generate_combined_invoices,
            send_combined_invoice,
        )

        for draft in generate_combined_invoices():
            send_combined_invoice(draft)
        self.invoice.refresh_from_db()

    def test_the_buyer_sees_how_to_pay(self):
        self.client.force_login(self.buyer)

        response = self.client.get(self.url)

        self.assertContains(response, 'How to Pay')
        self.assertContains(response, '@brynn-vale')
        self.assertContains(response, 'pay@brynnvale.example')

    def test_an_unbilled_sale_shows_no_payment_details_to_anyone(self):
        """Not yet invoiced is not yet owed — see the Invoicing section."""
        unbilled = Invoice.objects.create(
            listing=self._listing(title='Not Billed Yet'),
            buyer=self.buyer, seller=self.seller,
            amount='9.00', shipping_fee='1.00',
        )
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('invoice_detail', kwargs={'pk': unbilled.pk})
        )

        self.assertContains(response, 'nothing to pay yet')
        self.assertNotContains(response, 'How to Pay')
        for handle in HANDLES.values():
            self.assertNotContains(response, handle)

    def test_another_buyer_cannot_read_them(self):
        other = User.objects.create_user('nosy', 'nosy@example.com', 'pw')
        self.client.force_login(other)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_an_anonymous_visitor_cannot_read_them(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_staff_can_read_them(self):
        staff = User.objects.create_user(
            'payboss', 'payboss@example.com', 'pw', is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(self.url)

        self.assertContains(response, '@brynn-vale')


class PaymentAdminTests(PaymentFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        staff = User.objects.create_user(
            'adminboss', 'adminboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

    def test_the_profile_page_groups_them_under_payment_methods(self):
        url = reverse(
            'admin:auctions_userprofile_change', args=[self.seller.profile.pk]
        )

        response = self.client.get(url)

        self.assertContains(response, 'Payment methods')
        for field in ('venmo_info', 'paypal_info', 'cashapp_info',
                      'apple_pay_info', 'google_pay_info', 'zelle_info'):
            self.assertContains(response, f'name="{field}"')

    def test_they_are_editable_from_the_user_page_too(self):
        """Flagging a seller and filling these in should be one visit."""
        url = reverse('admin:auth_user_change', args=[self.seller.pk])

        response = self.client.get(url)

        self.assertContains(response, 'venmo_info')
        self.assertContains(response, 'zelle_info')
