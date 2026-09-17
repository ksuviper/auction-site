"""
Sellers are offered their own rate, and never both rates at once.

The membership itself is unchanged: a seller's subscription opens exactly the
gates a buyer's does. Only the price differs, and which set of cards the
subscribe page shows.

The case worth being careful about is the one at the end — flagging somebody a
seller must not touch a membership they already hold. Their rate was agreed when
they signed up, and silently moving them onto a different one would be a
surprise on their statement.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from auctions.models import Subscription, SubscriptionPlan, UserProfile
from auctions.utils import has_active_subscription

User = get_user_model()


def price_plans(**overrides):
    """Give all four plans a price and a PayPal id so they are sellable."""
    defaults = {
        ('buyer', 'monthly'): ('9.99', 'P-BUY-M'),
        ('buyer', 'yearly'): ('99.99', 'P-BUY-Y'),
        ('seller', 'monthly'): ('19.99', 'P-SELL-M'),
        ('seller', 'yearly'): ('199.99', 'P-SELL-Y'),
    }
    defaults.update(overrides)
    for (audience, cycle), (price, plan_id) in defaults.items():
        SubscriptionPlan.objects.update_or_create(
            audience=audience, billing_cycle=cycle,
            defaults={
                'price': Decimal(price), 'paypal_plan_id': plan_id,
                'is_active': True,
            },
        )


class WhichPlansAreOfferedTests(TestCase):
    def setUp(self):
        price_plans()
        self.buyer = User.objects.create_user('abuyer', 'abuyer@example.com', 'pw')
        self.seller = User.objects.create_user('aseller', 'aseller@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)

    def test_a_buyer_sees_only_buyer_prices(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '9.99')
        self.assertContains(response, '99.99')
        self.assertNotContains(response, '19.99')
        self.assertNotContains(response, '199.99')

    def test_a_seller_sees_only_seller_prices(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '19.99')
        self.assertContains(response, '199.99')
        self.assertNotContains(response, '>$9.99<')
        self.assertNotContains(response, '>$99.99<')

    def test_a_seller_is_told_the_rate_is_a_seller_rate(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, 'Seller pricing')
        self.assertContains(response, 'Seller Monthly')
        self.assertContains(response, 'Seller Annual')

    def test_a_buyer_is_not_shown_seller_wording(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('subscribe'))

        self.assertNotContains(response, 'Seller pricing')

    def test_the_annual_saving_is_worked_out_from_the_rate_on_offer(self):
        """A seller's saving must come from seller prices, not buyer ones."""
        self.client.force_login(self.seller)

        savings = self.client.get(reverse('subscribe')).context['yearly_savings']

        self.assertEqual(savings, Decimal('19.99') * 12 - Decimal('199.99'))


class CheckoutTests(TestCase):
    def setUp(self):
        price_plans()
        self.seller = User.objects.create_user('cseller', 'cseller@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)
        self.buyer = User.objects.create_user('cbuyer', 'cbuyer@example.com', 'pw')

    def _approve(self):
        """PayPal accepting a subscription creation."""
        response = type('R', (), {
            'status_code': 201,
            'json': lambda self: {
                'id': 'I-SUB-1',
                'links': [{'rel': 'approve', 'href': 'https://paypal.test/approve'}],
            },
            'text': '',
        })()
        return patch('auctions.subscription_views.paypal_request', return_value=response)

    def test_a_seller_checkout_uses_the_seller_plan(self):
        self.client.force_login(self.seller)

        with self._approve():
            self.client.post(reverse('subscribe_create', kwargs={'plan': 'monthly'}))

        subscription = Subscription.objects.get(user=self.seller)
        self.assertEqual(subscription.paypal_plan_id, 'P-SELL-M')
        self.assertEqual(subscription.plan_audience, 'seller')

    def test_a_seller_yearly_checkout_uses_the_seller_yearly_plan(self):
        self.client.force_login(self.seller)

        with self._approve():
            self.client.post(reverse('subscribe_create', kwargs={'plan': 'yearly'}))

        self.assertEqual(
            Subscription.objects.get(user=self.seller).paypal_plan_id, 'P-SELL-Y'
        )

    def test_a_buyer_checkout_still_uses_the_buyer_plan(self):
        self.client.force_login(self.buyer)

        with self._approve():
            self.client.post(reverse('subscribe_create', kwargs={'plan': 'monthly'}))

        subscription = Subscription.objects.get(user=self.buyer)
        self.assertEqual(subscription.paypal_plan_id, 'P-BUY-M')
        self.assertEqual(subscription.plan_audience, 'buyer')

    def test_a_seller_cannot_reach_the_buyer_plan_by_posting_directly(self):
        """
        The plan is chosen server-side from the account, so the URL carries the
        billing cycle only — there is nothing in it to tamper with.
        """
        self.client.force_login(self.seller)

        with self._approve():
            self.client.post(reverse('subscribe_create', kwargs={'plan': 'monthly'}))

        self.assertEqual(
            Subscription.objects.get(user=self.seller).paypal_plan_id, 'P-SELL-M'
        )


class NoSellerPricingYetTests(TestCase):
    """Before anyone has decided what sellers pay."""

    def setUp(self):
        price_plans()
        SubscriptionPlan.objects.filter(audience='seller').update(
            price=Decimal('0.00'), paypal_plan_id='', is_active=False
        )
        self.seller = User.objects.create_user('dseller', 'dseller@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)
        self.client.force_login(self.seller)

    def test_the_seller_is_told_rather_than_shown_a_blank_price(self):
        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, 'not available to buy just yet')
        self.assertTrue(response.context['plans_unavailable'])

    def test_no_checkout_button_is_offered(self):
        response = self.client.get(reverse('subscribe'))

        self.assertNotContains(response, 'Subscribe with PayPal')

    def test_posting_a_checkout_anyway_is_refused_without_calling_paypal(self):
        with patch('auctions.subscription_views.paypal_request') as paypal:
            response = self.client.post(
                reverse('subscribe_create', kwargs={'plan': 'monthly'})
            )

        paypal.assert_not_called()
        self.assertRedirects(response, reverse('subscribe'))
        self.assertFalse(Subscription.objects.filter(user=self.seller).exists())

    def test_buyers_are_unaffected(self):
        buyer = User.objects.create_user('dbuyer', 'dbuyer@example.com', 'pw')
        self.client.force_login(buyer)

        response = self.client.get(reverse('subscribe'))

        self.assertContains(response, '9.99')
        self.assertNotContains(response, 'not available to buy just yet')


class TheGateIsUnchangedTests(TestCase):
    """
    A seller's membership must satisfy the same check a buyer's does. This is
    the assurance that adding a second rate did not quietly add a second gate.
    """

    def setUp(self):
        price_plans()
        self.seller = User.objects.create_user('eseller', 'eseller@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)

    def test_an_active_seller_membership_passes_the_gate(self):
        Subscription.objects.create(
            user=self.seller, plan='monthly', plan_audience='seller',
            status='active',
        )

        self.assertTrue(has_active_subscription(self.seller))

    def test_a_lapsed_seller_membership_does_not_pass(self):
        Subscription.objects.create(
            user=self.seller, plan='monthly', plan_audience='seller',
            status='lapsed',
        )

        self.assertFalse(has_active_subscription(self.seller))

    def test_buyer_and_seller_memberships_behave_identically(self):
        buyer = User.objects.create_user('ebuyer', 'ebuyer@example.com', 'pw')
        Subscription.objects.create(
            user=buyer, plan='monthly', plan_audience='buyer', status='active'
        )
        Subscription.objects.create(
            user=self.seller, plan='monthly', plan_audience='seller',
            status='active',
        )

        self.assertEqual(
            has_active_subscription(buyer), has_active_subscription(self.seller)
        )


class FlaggingSomeoneLaterTests(TestCase):
    def setUp(self):
        price_plans()
        self.member = User.objects.create_user('later', 'later@example.com', 'pw')
        self.subscription = Subscription.objects.create(
            user=self.member, plan='monthly', plan_audience='buyer',
            status='active', paypal_plan_id='P-BUY-M',
        )

    def test_an_existing_membership_is_left_alone(self):
        """
        They agreed a price when they signed up. Moving them to another rate
        without asking would show up on their statement as a surprise.
        """
        UserProfile.objects.filter(user=self.member).update(is_seller=True)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan_audience, 'buyer')
        self.assertEqual(self.subscription.paypal_plan_id, 'P-BUY-M')
        self.assertEqual(self.subscription.status, 'active')

    def test_the_seller_rate_applies_only_if_they_subscribe_again(self):
        UserProfile.objects.filter(user=self.member).update(is_seller=True)
        self.member.refresh_from_db()
        self.client.force_login(self.member)

        offered = SubscriptionPlan.offered_to(self.member)

        self.assertEqual(offered['monthly'].paypal_plan_id, 'P-SELL-M')


class MembershipPageTests(TestCase):
    def setUp(self):
        price_plans()
        self.seller = User.objects.create_user('mseller', 'mseller@example.com', 'pw')
        UserProfile.objects.filter(user=self.seller).update(is_seller=True)

    def test_a_seller_membership_is_named_as_one(self):
        Subscription.objects.create(
            user=self.seller, plan='monthly', plan_audience='seller',
            status='active',
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse('membership'))

        self.assertContains(response, 'Seller Monthly')

    def test_a_buyer_membership_is_not_relabelled(self):
        buyer = User.objects.create_user('mbuyer', 'mbuyer@example.com', 'pw')
        Subscription.objects.create(
            user=buyer, plan='monthly', plan_audience='buyer', status='active'
        )
        self.client.force_login(buyer)

        response = self.client.get(reverse('membership'))

        self.assertContains(response, 'Monthly')
        self.assertNotContains(response, 'Seller Monthly')


class WelcomeEmailTests(TestCase):
    def test_a_seller_is_welcomed_to_a_seller_membership(self):
        from auctions.subscription_views import SubscribeReturnView

        seller = User.objects.create_user('wseller', 'wseller@example.com', 'pw')
        subscription = Subscription.objects.create(
            user=seller, plan='monthly', plan_audience='seller', status='active'
        )

        from django.core import mail
        SubscribeReturnView()._send_welcome_email(seller, subscription)

        self.assertIn('Seller Monthly membership', mail.outbox[0].body)

    def test_a_buyer_email_is_unchanged(self):
        from django.core import mail

        from auctions.subscription_views import SubscribeReturnView

        buyer = User.objects.create_user('wbuyer', 'wbuyer@example.com', 'pw')
        subscription = Subscription.objects.create(
            user=buyer, plan='monthly', plan_audience='buyer', status='active'
        )

        SubscribeReturnView()._send_welcome_email(buyer, subscription)

        self.assertIn('Your Monthly membership is now active', mail.outbox[0].body)
        self.assertNotIn('Seller', mail.outbox[0].body)
