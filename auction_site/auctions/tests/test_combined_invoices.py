"""Tests for combined invoicing.

The behavioural change is large and mostly about what *stops* happening: a
buyer used to be emailed the moment each auction closed or each Buy It Now
purchase went through. They now hear nothing until an admin generates a
combined invoice and sends it, and that email is the notification.

The rules worth pinning:

  * Everything unbilled for one buyer+seller pair lands on one invoice, however
    long ago it closed. There is no batch boundary.
  * A second Generate run adds to an existing draft rather than opening a rival
    one — the "open running tab". Enforced in the database, not just the code.
  * Once sent, the tab closes: the next win starts a fresh draft.
  * Sending emails exactly once, with the right total.
  * Buyers cannot read each other's invoices, and only staff can generate,
    review or send.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    CombinedInvoice,
    Invoice,
    Subscription,
    UserProfile,
)
from auctions.services import generate_combined_invoices, send_combined_invoice
from auctions.tests.utils import make_seller

User = get_user_model()

DASHBOARD_URL = reverse('combined_invoice_dashboard')
GENERATE_URL = reverse('combined_invoice_generate')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CombinedInvoiceTestCase(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.rowan = make_seller(
            'rowan', first_name='Rowan', last_name='Fields',
            paypal_info='pay@rowan.example',
        )
        self.mallory = make_seller(
            'mallory', first_name='Mallory', paypal_info='pay@mallory.example',
        )
        self.buyer = User.objects.create_user(
            'cass', 'cass@example.com', 'pw', first_name='Cass'
        )
        self.staff = User.objects.create_user(
            'invoiceboss', 'invoiceboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        mail.outbox = []

    def _listing(self, seller=None, *, title='Plant', **kwargs):
        now = timezone.now()
        defaults = dict(
            title=title, category=self.category, seller=seller or self.rowan,
            start_price='10.00', starts_at=now - timedelta(days=2),
            ends_at=now + timedelta(days=4), is_active=True,
        )
        defaults.update(kwargs)
        return AuctionListing.objects.create(**defaults)

    def _sale(self, *, seller=None, buyer=None, amount='20.00',
              shipping='5.00', quantity=1, title='Plant'):
        """An unbilled Invoice row — what generation gathers up."""
        return Invoice.objects.create(
            listing=self._listing(seller=seller, title=title),
            buyer=buyer or self.buyer,
            seller=seller or self.rowan,
            quantity=quantity,
            amount=amount,
            shipping_fee=shipping,
        )

    def as_staff(self):
        self.client.force_login(self.staff)


class GenerationTests(CombinedInvoiceTestCase):
    def test_it_groups_a_buyers_sales_from_one_seller_onto_one_invoice(self):
        self._sale(title='One', amount='20.00')
        self._sale(title='Two', amount='30.00')

        drafts = generate_combined_invoices()

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].line_items.count(), 2)
        self.assertEqual(drafts[0].subtotal, Decimal('50.00'))

    def test_different_sellers_get_separate_invoices(self):
        """A buyer pays each grower separately, so each gets its own bill."""
        self._sale(seller=self.rowan, title='Rowan Plant')
        self._sale(seller=self.mallory, title='Mallory Plant')

        drafts = generate_combined_invoices()

        self.assertEqual(len(drafts), 2)
        self.assertEqual(
            {d.seller_id for d in drafts}, {self.rowan.pk, self.mallory.pk}
        )
        for draft in drafts:
            self.assertEqual(draft.line_items.count(), 1)

    def test_different_buyers_get_separate_invoices(self):
        other = User.objects.create_user('dara', 'dara@example.com', 'pw')
        self._sale(buyer=self.buyer)
        self._sale(buyer=other)

        drafts = generate_combined_invoices()

        self.assertEqual(len(drafts), 2)
        self.assertEqual({d.buyer_id for d in drafts}, {self.buyer.pk, other.pk})

    def test_it_ignores_how_long_ago_a_sale_closed(self):
        """An open running tab, not a batch: there is no time window."""
        old = self._sale(title='Ancient')
        Invoice.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=120)
        )
        self._sale(title='Recent')

        drafts = generate_combined_invoices()

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].line_items.count(), 2)

    def test_a_second_run_adds_to_the_existing_draft(self):
        self._sale(title='First')
        generate_combined_invoices()

        self._sale(title='Second')
        drafts = generate_combined_invoices()

        self.assertEqual(CombinedInvoice.objects.count(), 1)
        self.assertEqual(drafts[0].line_items.count(), 2)

    def test_a_second_run_with_nothing_new_does_nothing(self):
        self._sale()
        generate_combined_invoices()

        self.assertEqual(generate_combined_invoices(), [])
        self.assertEqual(CombinedInvoice.objects.count(), 1)

    def test_generating_with_no_sales_at_all_is_a_no_op(self):
        self.assertEqual(generate_combined_invoices(), [])
        self.assertFalse(CombinedInvoice.objects.exists())

    def test_a_sent_invoice_does_not_reopen(self):
        """Once billed, the tab is closed; the next win starts a fresh one."""
        self._sale(title='First')
        first = generate_combined_invoices()[0]
        send_combined_invoice(first)

        self._sale(title='Later')
        drafts = generate_combined_invoices()

        self.assertEqual(CombinedInvoice.objects.count(), 2)
        self.assertNotEqual(drafts[0].pk, first.pk)
        self.assertEqual(drafts[0].line_items.count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.line_items.count(), 1)

    def test_already_billed_sales_are_not_picked_up_again(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        generate_combined_invoices()

        self.assertEqual(draft.line_items.count(), 1)

    def test_a_manually_created_invoice_is_billed_too(self):
        """Off-platform sales enter as Invoice rows and belong on the tab."""
        Invoice.objects.create(
            item_description='A plant sold at the show',
            buyer=self.buyer, seller=self.rowan,
            amount='15.00', shipping_fee='0.00', is_manually_created=True,
        )

        drafts = generate_combined_invoices()

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].subtotal, Decimal('15.00'))

    def test_the_database_refuses_a_second_open_draft_for_a_pair(self):
        """
        The code groups carefully, but two admins clicking Generate at the same
        moment is exactly the case only a constraint catches.
        """
        CombinedInvoice.objects.create(
            buyer=self.buyer, seller=self.rowan, status='draft'
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CombinedInvoice.objects.create(
                    buyer=self.buyer, seller=self.rowan, status='draft'
                )

    def test_two_sent_invoices_for_a_pair_are_allowed(self):
        """The constraint is on open drafts only — history accumulates."""
        for _ in range(2):
            CombinedInvoice.objects.create(
                buyer=self.buyer, seller=self.rowan, status='sent',
                sent_at=timezone.now(),
            )

        self.assertEqual(CombinedInvoice.objects.filter(status='sent').count(), 2)


class TotalsTests(CombinedInvoiceTestCase):
    def test_totals_add_up(self):
        self._sale(amount='20.00', shipping='5.00')
        self._sale(amount='30.00', shipping='7.00')

        draft = generate_combined_invoices()[0]

        self.assertEqual(draft.subtotal, Decimal('50.00'))
        self.assertEqual(draft.total_shipping, Decimal('12.00'))
        self.assertEqual(draft.total, Decimal('62.00'))

    def test_a_shipping_override_replaces_the_summed_fees(self):
        """Several plants in one box cost less than the fees added up."""
        self._sale(amount='20.00', shipping='5.00')
        self._sale(amount='30.00', shipping='7.00')
        draft = generate_combined_invoices()[0]

        draft.shipping_override = Decimal('8.00')
        draft.save()

        self.assertEqual(draft.summed_shipping, Decimal('12.00'))
        self.assertEqual(draft.total_shipping, Decimal('8.00'))
        self.assertEqual(draft.total, Decimal('58.00'))

    def test_an_override_of_zero_means_free_shipping_not_no_override(self):
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]

        draft.shipping_override = Decimal('0.00')
        draft.save()

        self.assertEqual(draft.total_shipping, Decimal('0.00'))
        self.assertEqual(draft.total, Decimal('20.00'))

    def test_a_discount_comes_off_the_total(self):
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]

        draft.discount_amount = Decimal('3.00')
        draft.save()

        self.assertEqual(draft.total, Decimal('22.00'))

    def test_money_always_carries_two_decimal_places(self):
        """
        SUM() returns Decimal('50') for 20.00 + 30.00 on SQLite.

        Unquantized, that reaches an email and the review page as "$50", which
        does not read as a price.
        """
        self._sale(amount='20.00', shipping='5.00')
        self._sale(amount='30.00', shipping='0.00')
        draft = generate_combined_invoices()[0]

        self.assertEqual(str(draft.subtotal), '50.00')
        self.assertEqual(str(draft.summed_shipping), '5.00')
        self.assertEqual(str(draft.total_shipping), '5.00')
        self.assertEqual(str(draft.total), '55.00')

    def test_an_empty_invoice_totals_zero_rather_than_erroring(self):
        draft = CombinedInvoice.objects.create(
            buyer=self.buyer, seller=self.rowan
        )

        self.assertEqual(draft.subtotal, Decimal('0.00'))
        self.assertEqual(draft.total_shipping, Decimal('0.00'))
        self.assertEqual(draft.total, Decimal('0.00'))


class SendingTests(CombinedInvoiceTestCase):
    def test_sending_emails_the_buyer_exactly_once(self):
        self._sale(title='One')
        self._sale(title='Two')
        draft = generate_combined_invoices()[0]

        self.assertTrue(send_combined_invoice(draft))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['cass@example.com'])

    def test_the_email_itemises_everything_and_states_the_total(self):
        self._sale(title='Spider Ballerina', amount='20.00', shipping='5.00')
        self._sale(title='Double Trouble', amount='30.00', shipping='7.00')
        draft = generate_combined_invoices()[0]
        draft.shipping_override = Decimal('8.00')
        draft.discount_amount = Decimal('3.00')
        draft.save()

        send_combined_invoice(draft)
        body = mail.outbox[0].body

        self.assertIn('Spider Ballerina', body)
        self.assertIn('Double Trouble', body)
        self.assertIn('$50.00', body)     # subtotal
        self.assertIn('$8.00', body)      # overridden shipping
        self.assertIn('-$3.00', body)     # discount
        self.assertIn('$55.00', body)     # 50 + 8 - 3

    def test_the_email_carries_the_sellers_payment_details(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        send_combined_invoice(draft)

        self.assertIn('pay@rowan.example', mail.outbox[0].body)

    def test_the_email_names_the_seller_in_its_subject(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        send_combined_invoice(draft)

        self.assertIn('Rowan Fields', mail.outbox[0].subject)

    def test_a_note_reaches_the_buyer(self):
        self._sale()
        draft = generate_combined_invoices()[0]
        draft.notes = 'Shipping the week of the 12th.'
        draft.save()

        send_combined_invoice(draft)

        self.assertIn('Shipping the week of the 12th.', mail.outbox[0].body)

    def test_sending_marks_it_sent_and_stamps_the_time(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        send_combined_invoice(draft)

        draft.refresh_from_db()
        self.assertEqual(draft.status, 'sent')
        self.assertIsNotNone(draft.sent_at)

    def test_sending_marks_the_line_items_sent_too(self):
        """So the older per-invoice views do not show them as outstanding."""
        self._sale()
        draft = generate_combined_invoices()[0]

        send_combined_invoice(draft)

        self.assertTrue(all(i.is_sent for i in draft.line_items.all()))

    def test_sending_twice_emails_once(self):
        self._sale()
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)

        self.assertFalse(send_combined_invoice(draft))

        self.assertEqual(len(mail.outbox), 1)

    def test_a_buyer_with_no_email_is_marked_sent_without_crashing(self):
        nameless = User.objects.create_user('noemail', '', 'pw')
        Invoice.objects.create(
            listing=self._listing(), buyer=nameless, seller=self.rowan,
            amount='10.00', shipping_fee='0.00',
        )
        draft = generate_combined_invoices()[0]

        self.assertTrue(send_combined_invoice(draft))

        self.assertEqual(mail.outbox, [])
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'sent')

    def test_a_seller_with_no_payment_details_still_sends(self):
        bare = make_seller('bare', paypal_info='')
        Invoice.objects.create(
            listing=self._listing(seller=bare), buyer=self.buyer, seller=bare,
            amount='10.00', shipping_fee='0.00',
        )
        draft = generate_combined_invoices()[0]

        send_combined_invoice(draft)

        self.assertIn('ask the seller', mail.outbox[0].body)


class AutoSendRemovedTests(CombinedInvoiceTestCase):
    """
    The breaking change: nothing emails a buyer at close or at purchase.

    The Invoice rows are still created — that is what invoicing works from.
    """

    def test_closing_an_auction_creates_an_invoice_but_emails_no_buyer(self):
        from django.core.management import call_command

        now = timezone.now()
        listing = self._listing(
            title='Ending Now',
            starts_at=now - timedelta(days=5), ends_at=now - timedelta(minutes=1),
        )
        from auctions.models import Bid
        Bid.objects.create(listing=listing, bidder=self.buyer, amount='25.00')
        mail.outbox = []

        call_command('close_ended_auctions')

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(invoice.buyer, self.buyer)
        self.assertEqual(invoice.amount, Decimal('25.00'))
        self.assertIsNone(invoice.combined_invoice)
        buyer_mail = [m for m in mail.outbox if 'cass@example.com' in m.to]
        self.assertEqual(buyer_mail, [], f'buyer was emailed: {buyer_mail}')

    def test_closing_an_auction_still_tells_the_seller(self):
        """An internal heads-up, not a request for money — it stays."""
        from django.core.management import call_command

        from auctions.models import Bid

        now = timezone.now()
        listing = self._listing(
            title='Ending Now',
            starts_at=now - timedelta(days=5), ends_at=now - timedelta(minutes=1),
        )
        Bid.objects.create(listing=listing, bidder=self.buyer, amount='25.00')
        mail.outbox = []

        call_command('close_ended_auctions')

        seller_mail = [m for m in mail.outbox if self.rowan.email in m.to]
        self.assertEqual(len(seller_mail), 1)
        self.assertIn('do not chase them for payment', seller_mail[0].body)

    def test_the_winner_email_helper_is_gone(self):
        """
        Removed rather than left unused.

        A method that emails a buyer at auction close is one wiring change away
        from asking for payment twice.
        """
        from auctions.management.commands.close_ended_auctions import Command

        self.assertFalse(hasattr(Command, '_email_winner'))

    def test_buying_now_creates_an_invoice_but_emails_no_buyer(self):
        listing = self._listing(
            title='Buy Me', listing_type='buy_now', buy_now_price='18.00',
            quantity_available=4,
        )
        Subscription.objects.create(
            user=self.buyer, plan='monthly', status='active'
        )
        UserProfile.objects.filter(user=self.buyer).update(country='US')
        self.client.force_login(self.buyer)
        mail.outbox = []

        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 2}
        )

        invoice = Invoice.objects.get(listing=listing)
        self.assertEqual(invoice.quantity, 2)
        self.assertIsNone(invoice.combined_invoice)
        buyer_mail = [m for m in mail.outbox if 'cass@example.com' in m.to]
        self.assertEqual(buyer_mail, [], f'buyer was emailed: {buyer_mail}')

    def test_buying_now_still_tells_the_seller(self):
        listing = self._listing(
            title='Buy Me', listing_type='buy_now', buy_now_price='18.00',
            quantity_available=4,
        )
        Subscription.objects.create(
            user=self.buyer, plan='monthly', status='active'
        )
        UserProfile.objects.filter(user=self.buyer).update(country='US')
        self.client.force_login(self.buyer)
        mail.outbox = []

        self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}), {'quantity': 1}
        )

        seller_mail = [m for m in mail.outbox if self.rowan.email in m.to]
        self.assertEqual(len(seller_mail), 1)

    def test_the_purchase_confirmation_does_not_promise_payment_details(self):
        listing = self._listing(
            title='Buy Me', listing_type='buy_now', buy_now_price='18.00',
            quantity_available=4,
        )
        Subscription.objects.create(
            user=self.buyer, plan='monthly', status='active'
        )
        UserProfile.objects.filter(user=self.buyer).update(country='US')
        self.client.force_login(self.buyer)

        response = self.client.post(
            reverse('buy_now', kwargs={'pk': listing.pk}),
            {'quantity': 1}, follow=True,
        )

        self.assertContains(response, 'Purchase complete')
        self.assertContains(response, 'We&#x27;ll email you an invoice')
        # An unbilled invoice is a record, not a bill.
        self.assertContains(response, 'there is nothing to pay yet')
        self.assertNotContains(response, 'How to Pay')

    def test_an_invoice_shows_how_to_pay_once_it_has_been_billed(self):
        invoice = self._sale()
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('invoice_detail', kwargs={'pk': invoice.pk})
        )

        self.assertContains(response, 'How to Pay')
        self.assertContains(response, 'pay@rowan.example')
        self.assertNotContains(response, 'nothing to pay yet')


class StaffWorkflowTests(CombinedInvoiceTestCase):
    def test_the_dashboard_counts_what_is_not_yet_invoiced(self):
        self._sale(amount='20.00')
        self._sale(amount='30.00')
        self.as_staff()

        response = self.client.get(DASHBOARD_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unbilled_count'], 2)
        self.assertEqual(response.context['unbilled_value'], Decimal('50.00'))
        self.assertEqual(response.context['unbilled_pairs'], 1)

    def test_generating_from_the_dashboard_creates_drafts_and_sends_nothing(self):
        self._sale()
        self.as_staff()

        response = self.client.post(GENERATE_URL, follow=True)

        self.assertRedirects(response, DASHBOARD_URL)
        self.assertEqual(CombinedInvoice.objects.count(), 1)
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Nothing has been emailed yet')

    def test_generating_with_nothing_outstanding_says_so(self):
        self.as_staff()

        response = self.client.post(GENERATE_URL, follow=True)

        self.assertContains(response, 'Nothing to invoice')

    def test_the_review_page_shows_the_line_items_and_totals(self):
        self._sale(title='Spider Ballerina', amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]
        self.as_staff()

        response = self.client.get(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Spider Ballerina')
        self.assertContains(response, '$25.00')
        self.assertContains(response, 'Draft — not sent')

    def test_saving_the_review_form_changes_nothing_but_the_draft(self):
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]
        self.as_staff()

        response = self.client.post(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk}),
            {'action': 'save', 'shipping_override': '3.00',
             'discount_amount': '1.00', 'notes': 'Careful with this one.'},
            follow=True,
        )

        draft.refresh_from_db()
        self.assertEqual(draft.shipping_override, Decimal('3.00'))
        self.assertEqual(draft.discount_amount, Decimal('1.00'))
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'the buyer has not been emailed')

    def test_sending_from_the_review_page_applies_the_edits_on_screen(self):
        """
        Save-and-send in one submit.

        Sending the last-saved numbers instead would quietly discard the
        adjustment the admin is looking at.
        """
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]
        self.as_staff()

        self.client.post(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk}),
            {'action': 'send', 'shipping_override': '2.00',
             'discount_amount': '0', 'notes': ''},
        )

        draft.refresh_from_db()
        self.assertEqual(draft.status, 'sent')
        self.assertEqual(draft.total, Decimal('22.00'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('$22.00', mail.outbox[0].body)

    def test_a_discount_larger_than_the_bill_is_refused(self):
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]
        self.as_staff()

        response = self.client.post(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk}),
            {'action': 'send', 'shipping_override': '', 'discount_amount': '99'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot make the total negative')
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(mail.outbox, [])

    def test_a_sent_invoice_cannot_be_edited_from_the_review_page(self):
        self._sale(amount='20.00', shipping='5.00')
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)
        mail.outbox = []
        self.as_staff()

        self.client.post(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk}),
            {'action': 'send', 'shipping_override': '999', 'discount_amount': '0'},
        )

        draft.refresh_from_db()
        self.assertIsNone(draft.shipping_override)
        self.assertEqual(mail.outbox, [])

    def test_a_sent_invoice_reads_as_sent_and_offers_no_send_button(self):
        self._sale()
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)
        self.as_staff()

        response = self.client.get(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk})
        )

        self.assertContains(response, 'can no longer be changed')
        self.assertNotContains(response, 'Save &amp; send to buyer')

    def test_the_review_page_warns_when_the_seller_has_no_payment_details(self):
        bare = make_seller('bare', paypal_info='')
        Invoice.objects.create(
            listing=self._listing(seller=bare), buyer=self.buyer, seller=bare,
            amount='10.00', shipping_fee='0.00',
        )
        draft = generate_combined_invoices()[0]
        self.as_staff()

        response = self.client.get(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk})
        )

        self.assertContains(response, 'no payment details on file')

    def test_the_review_page_warns_when_the_buyer_has_no_email(self):
        nameless = User.objects.create_user('noemail', '', 'pw')
        Invoice.objects.create(
            listing=self._listing(), buyer=nameless, seller=self.rowan,
            amount='10.00', shipping_fee='0.00',
        )
        draft = generate_combined_invoices()[0]
        self.as_staff()

        response = self.client.get(
            reverse('combined_invoice_review', kwargs={'pk': draft.pk})
        )

        self.assertContains(response, 'no email address on file')


class AuthorizationTests(CombinedInvoiceTestCase):
    """Only staff generate, review or send. Buyers see only their own bills."""

    def _urls_for(self, draft):
        return [
            DASHBOARD_URL,
            reverse('combined_invoice_review', kwargs={'pk': draft.pk}),
        ]

    def test_an_anonymous_visitor_is_refused_everywhere(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        for url in self._urls_for(draft):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(GENERATE_URL).status_code, 403)

    def test_a_signed_in_buyer_is_refused_everywhere(self):
        self._sale()
        draft = generate_combined_invoices()[0]
        self.client.force_login(self.buyer)

        for url in self._urls_for(draft):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(GENERATE_URL).status_code, 403)

    def test_a_seller_account_is_refused_too(self):
        """Being flagged as a seller grants no admin powers."""
        self._sale()
        draft = generate_combined_invoices()[0]
        self.client.force_login(self.rowan)

        for url in self._urls_for(draft):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(GENERATE_URL).status_code, 403)

    def test_a_buyer_cannot_read_another_buyers_line_item(self):
        other = User.objects.create_user('dara', 'dara@example.com', 'pw')
        theirs = Invoice.objects.create(
            listing=self._listing(), buyer=other, seller=self.rowan,
            amount='40.00', shipping_fee='5.00',
        )
        self.client.force_login(self.buyer)

        response = self.client.get(
            reverse('invoice_detail', kwargs={'pk': theirs.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_generate_rejects_a_get(self):
        """It writes; a link or a crawler must not be able to trigger it."""
        self.as_staff()

        self.assertEqual(self.client.get(GENERATE_URL).status_code, 405)


class CombinedInvoiceAdminTests(CombinedInvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.as_staff()

    def test_the_changelist_filters_by_status_and_links_to_review(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        response = self.client.get(
            reverse('admin:auctions_combinedinvoice_changelist')
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'status')
        self.assertContains(
            response, reverse('combined_invoice_review', args=[draft.pk])
        )

    def test_a_sent_invoice_cannot_be_edited_in_the_admin(self):
        """The site would then disagree with the email in the buyer's inbox."""
        self._sale()
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)

        response = self.client.get(
            reverse('admin:auctions_combinedinvoice_change', args=[draft.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count('name="_save"'), 0)

    def test_a_sent_invoice_cannot_be_deleted_in_the_admin(self):
        """
        Deleting it would un-bill its line items via SET_NULL, and the next
        Generate run would bill the buyer a second time.
        """
        self._sale()
        draft = generate_combined_invoices()[0]
        send_combined_invoice(draft)

        self.client.post(
            reverse('admin:auctions_combinedinvoice_delete', args=[draft.pk]),
            {'post': 'yes'},
        )

        self.assertTrue(CombinedInvoice.objects.filter(pk=draft.pk).exists())

    def test_deleting_a_draft_returns_its_items_to_the_unbilled_pool(self):
        """The useful case: regroup after a mistake."""
        invoice = self._sale()
        draft = generate_combined_invoices()[0]

        draft.delete()

        invoice.refresh_from_db()
        self.assertIsNone(invoice.combined_invoice)
        self.assertEqual(len(generate_combined_invoices()), 1)

    def test_the_line_items_inline_is_read_only(self):
        self._sale()
        draft = generate_combined_invoices()[0]

        response = self.client.get(
            reverse('admin:auctions_combinedinvoice_change', args=[draft.pk])
        )
        html = response.content.decode()

        self.assertContains(response, 'Line items')
        self.assertNotIn('line_items-0-amount', html)

    def test_the_sidebar_badge_counts_drafts(self):
        from auctions.admin import draft_invoice_count

        self.assertEqual(draft_invoice_count(None), '0')

        self._sale()
        generate_combined_invoices()

        self.assertEqual(draft_invoice_count(None), '1')
