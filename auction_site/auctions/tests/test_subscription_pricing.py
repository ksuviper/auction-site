"""
Prices live in the admin, and a change reaches PayPal or is not saved at all.

The failure that matters is the quiet one. PayPal keeps its own copy of the
price on the billing plan and charges from that, so saving a new number here
while PayPal refuses the update would leave the site advertising one figure and
PayPal collecting another — with nothing on screen to suggest anything went
wrong. The admin therefore syncs first and abandons the save if PayPal says no.

Every PayPal call is mocked. There are no credentials here, and these assert our
behaviour around the API rather than the API itself; the live sandbox run is a
manual step.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from auctions.models import SubscriptionPlan, UserProfile
from auctions.utils import PayPalPriceUpdateError, update_paypal_plan_price

User = get_user_model()


class FakeResponse:
    def __init__(self, status_code, text=''):
        self.status_code = status_code
        self.text = text


def plan(audience='buyer', cycle='monthly', price='9.99', plan_id='P-1'):
    obj, _ = SubscriptionPlan.objects.update_or_create(
        audience=audience, billing_cycle=cycle,
        defaults={
            'price': Decimal(price), 'paypal_plan_id': plan_id,
            'is_active': True,
        },
    )
    return obj


class PriceSyncHelperTests(TestCase):
    def test_a_successful_update_reports_success(self):
        with patch('auctions.paypal.paypal_request', return_value=FakeResponse(204)):
            self.assertTrue(update_paypal_plan_price(plan(), Decimal('12.00')))

    def test_it_posts_to_the_documented_endpoint(self):
        target = plan(plan_id='P-ABC')

        with patch(
            'auctions.paypal.paypal_request', return_value=FakeResponse(204)
        ) as call:
            update_paypal_plan_price(target, Decimal('12.00'))

        method, path = call.call_args[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(path, '/v1/billing/plans/P-ABC/update-pricing-schemes')

    def test_it_sends_the_price_in_the_shape_paypal_expects(self):
        target = plan()

        with patch(
            'auctions.paypal.paypal_request', return_value=FakeResponse(204)
        ) as call:
            update_paypal_plan_price(target, Decimal('12.50'))

        scheme = call.call_args.kwargs['json']['pricing_schemes'][0]
        self.assertEqual(scheme['billing_cycle_sequence'], 1)
        self.assertEqual(
            scheme['pricing_scheme']['fixed_price'],
            {'value': '12.50', 'currency_code': 'USD'},
        )

    def test_a_rejection_raises_with_something_worth_showing_staff(self):
        with patch(
            'auctions.paypal.paypal_request',
            return_value=FakeResponse(422, 'INVALID_PLAN_ID'),
        ):
            with self.assertRaises(PayPalPriceUpdateError) as caught:
                update_paypal_plan_price(plan(), Decimal('12.00'))

        self.assertIn('422', str(caught.exception))
        self.assertIn('INVALID_PLAN_ID', str(caught.exception))

    def test_an_unreachable_paypal_raises_rather_than_passing_silently(self):
        with patch(
            'auctions.paypal.paypal_request', side_effect=OSError('connection reset')
        ):
            with self.assertRaises(PayPalPriceUpdateError):
                update_paypal_plan_price(plan(), Decimal('12.00'))

    def test_a_plan_with_no_paypal_id_is_refused_before_any_call(self):
        with patch('auctions.paypal.paypal_request') as call:
            with self.assertRaises(PayPalPriceUpdateError) as caught:
                update_paypal_plan_price(plan(plan_id=''), Decimal('12.00'))

        call.assert_not_called()
        self.assertIn('create_paypal_plans', str(caught.exception))


class AdminPriceChangeTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(
            'priceboss', 'priceboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)
        self.plan = plan(price='9.99')

    def url(self):
        return reverse('admin:auctions_subscriptionplan_change', args=[self.plan.pk])

    def form(self, **overrides):
        data = {
            'audience': self.plan.audience,
            'billing_cycle': self.plan.billing_cycle,
            'price': '14.99',
            'currency': 'USD',
            'paypal_plan_id': self.plan.paypal_plan_id,
            'is_active': 'on',
            '_save': 'Save',
        }
        data.update(overrides)
        return data

    def test_a_successful_change_is_saved_and_stamped(self):
        with patch('auctions.paypal.paypal_request', return_value=FakeResponse(204)):
            self.client.post(self.url(), self.form())

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price, Decimal('14.99'))
        self.assertIsNotNone(self.plan.last_synced_at)

    def test_a_rejected_change_leaves_the_price_alone(self):
        """
        The whole point. A saved price PayPal never accepted would have staff
        believing members are charged one amount while they are charged another.
        """
        with patch(
            'auctions.paypal.paypal_request',
            return_value=FakeResponse(422, 'INVALID_REQUEST'),
        ):
            self.client.post(self.url(), self.form())

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price, Decimal('9.99'))
        self.assertIsNone(self.plan.last_synced_at)

    def test_a_rejected_change_says_so_plainly(self):
        with patch(
            'auctions.paypal.paypal_request',
            return_value=FakeResponse(422, 'INVALID_REQUEST'),
        ):
            response = self.client.post(self.url(), self.form(), follow=True)

        body = response.content.decode()
        self.assertIn('Price NOT changed', body)
        self.assertIn('still being charged the old price', body)

    def test_saving_without_touching_the_price_calls_nothing(self):
        """Flipping is_active should not fire a pricing call at PayPal."""
        with patch('auctions.paypal.paypal_request') as call:
            self.client.post(self.url(), self.form(price='9.99', is_active=''))

        call.assert_not_called()
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_active)

    def test_the_admin_warns_that_current_members_are_repriced(self):
        """
        Verified against PayPal's documentation: a pricing update applies to
        everyone on the plan from their next billing date, not only to new
        sign-ups. Staff have to be told before they change a number.
        """
        body = self.client.get(self.url()).content.decode()

        self.assertIn('changes what current members pay', body)
        self.assertIn('next billing date', body)

    def test_the_retry_action_resends_without_retyping_the_price(self):
        with patch('auctions.paypal.paypal_request', return_value=FakeResponse(204)):
            self.client.post(
                reverse('admin:auctions_subscriptionplan_changelist'),
                {
                    'action': 'resync_with_paypal',
                    '_selected_action': [str(self.plan.pk)],
                },
                follow=True,
            )

        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.last_synced_at)

    def test_a_failed_retry_reports_and_leaves_the_stamp_unset(self):
        with patch(
            'auctions.paypal.paypal_request', return_value=FakeResponse(500, 'boom')
        ):
            response = self.client.post(
                reverse('admin:auctions_subscriptionplan_changelist'),
                {
                    'action': 'resync_with_paypal',
                    '_selected_action': [str(self.plan.pk)],
                },
                follow=True,
            )

        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.last_synced_at)
        self.assertIn('500', response.content.decode())


class PublicPricesComeFromTheDatabaseTests(TestCase):
    def setUp(self):
        plan('buyer', 'monthly', '9.99', 'P-BUY-M')
        plan('buyer', 'yearly', '99.99', 'P-BUY-Y')
        self.member = User.objects.create_user('shopper', 'shopper@example.com', 'pw')
        self.client.force_login(self.member)

    def test_a_price_change_shows_on_the_subscribe_page_at_once(self):
        SubscriptionPlan.objects.filter(
            audience='buyer', billing_cycle='monthly'
        ).update(price=Decimal('11.50'))

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '11.50')
        self.assertNotContains(response, '>$9.99<')

    def test_an_inactive_plan_is_not_offered(self):
        SubscriptionPlan.objects.filter(
            audience='buyer', billing_cycle='yearly'
        ).update(is_active=False)

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '9.99')
        self.assertNotContains(response, '99.99')

    def test_a_plan_with_no_paypal_id_is_not_offered(self):
        """It has never been created at PayPal, so its checkout cannot work."""
        SubscriptionPlan.objects.filter(
            audience='buyer', billing_cycle='yearly'
        ).update(paypal_plan_id='')

        response = self.client.get(reverse('subscribe'))

        self.assertNotContains(response, '99.99')

    def test_the_settings_price_is_no_longer_what_is_shown(self):
        """
        settings.PAYPAL_MONTHLY_PRICE seeds the row once and is never read
        again. If the page still followed it, this would show 9.99.
        """
        SubscriptionPlan.objects.filter(
            audience='buyer', billing_cycle='monthly'
        ).update(price=Decimal('42.00'))

        with self.settings(PAYPAL_MONTHLY_PRICE='9.99'):
            response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '42.00')


class SellerPricesAreSeparateTests(TestCase):
    def setUp(self):
        plan('buyer', 'monthly', '9.99', 'P-BUY-M')
        plan('seller', 'monthly', '19.99', 'P-SELL-M')
        self.seller = User.objects.create_user('sp', 'sp@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)

    def test_changing_the_seller_price_leaves_the_buyer_price_alone(self):
        SubscriptionPlan.objects.filter(
            audience='seller', billing_cycle='monthly'
        ).update(price=Decimal('24.99'))

        self.client.force_login(self.seller)
        seller_page = self.client.get(reverse('subscribe'))
        buyer = User.objects.create_user('bp', 'bp@example.com', 'pw')
        self.client.force_login(buyer)
        buyer_page = self.client.get(reverse('subscribe'))

        self.assertContains(seller_page, '24.99')
        self.assertContains(buyer_page, '9.99')
        self.assertNotContains(buyer_page, '24.99')


class SeededPlansTests(TestCase):
    """What migration 0026 leaves behind on an existing deployment."""

    def test_all_four_rows_exist(self):
        combinations = set(
            SubscriptionPlan.objects.values_list('audience', 'billing_cycle')
        )

        self.assertEqual(
            combinations,
            {('buyer', 'monthly'), ('buyer', 'yearly'),
             ('seller', 'monthly'), ('seller', 'yearly')},
        )

    def test_the_buyer_rows_carry_the_prices_the_site_was_running_on(self):
        monthly = SubscriptionPlan.objects.get(
            audience='buyer', billing_cycle='monthly'
        )

        self.assertEqual(monthly.price, Decimal('9.99'))

    def test_seller_rows_are_held_inactive_until_someone_prices_them(self):
        for row in SubscriptionPlan.objects.filter(audience='seller'):
            with self.subTest(cycle=row.billing_cycle):
                self.assertFalse(row.is_active)
                self.assertFalse(row.is_sellable)

    def test_only_one_row_can_exist_per_audience_and_cycle(self):
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            SubscriptionPlan.objects.create(
                audience='buyer', billing_cycle='monthly', price=Decimal('1.00')
            )
