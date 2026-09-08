"""Security audit checks (backlog item 21).

These pin down properties that later batches could quietly erode. Each one
answers a question from the audit brief with a request rather than a reading
of the code, so a refactor that drops a decorator or widens a gate fails here
rather than in production.

Several focus on the seller flag specifically: the seller merge touched
User/UserProfile heavily, and ``is_seller`` must grant nothing at all beyond
what it is for — appearing in a picker and a directory, and a read-only
dashboard.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Invoice,
    Subscription,
    UserProfile,
)
from auctions.services import generate_combined_invoices
from auctions.tests.utils import LOGIN_CODE_URL, is_signed_in, make_seller, sign_in

User = get_user_model()
PASSWORD = 'audit-passphrase-42'


def verified(user):
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={'verified': True, 'primary': True}
    )
    return user


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SellerFlagGrantsNothingTests(TestCase):
    """
    is_seller must not bypass any gate that applies to an ordinary member.

    The merge put the flag on the same row as is_approved and
    email_login_code_enabled; these confirm the adapter never looks at it.
    """

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def test_an_unapproved_seller_cannot_log_in(self):
        seller = verified(make_seller('unapproved', email='unapproved@example.com'))
        seller.set_password(PASSWORD)
        seller.save()
        UserProfile.objects.filter(user=seller).update(is_approved=False)

        sign_in(self.client, 'unapproved@example.com', PASSWORD)

        self.assertFalse(is_signed_in(self.client))

    def test_a_seller_with_an_unverified_email_cannot_log_in(self):
        seller = make_seller('unverified', email='unverified@example.com')
        seller.set_password(PASSWORD)
        seller.save()
        EmailAddress.objects.filter(user=seller).delete()

        sign_in(self.client, 'unverified@example.com', PASSWORD)

        self.assertFalse(is_signed_in(self.client))

    def test_a_seller_still_needs_the_emailed_login_code(self):
        seller = verified(make_seller('coded', email='coded@example.com'))
        seller.set_password(PASSWORD)
        seller.save()

        response = self.client.post(
            '/accounts/login/',
            {'login': 'coded@example.com', 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.20', follow=True,
        )

        self.assertFalse(is_signed_in(self.client))
        self.assertEqual(response.request['PATH_INFO'], LOGIN_CODE_URL)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_seller_flagged_staff_account_cannot_switch_the_code_off(self):
        staff = verified(make_seller(
            'staffseller', email='staffseller@example.com',
            is_staff=True, is_superuser=True,
        ))
        self.client.force_login(staff)

        response = self.client.post(reverse('toggle_email_login_code'))

        self.assertEqual(response.status_code, 403)
        staff.profile.refresh_from_db()
        self.assertTrue(staff.profile.email_login_code_enabled)

    def test_a_seller_cannot_reach_any_staff_page(self):
        seller = make_seller('nopower')
        self.client.force_login(seller)
        staff_urls = [
            reverse('weekly_setup'),
            reverse('invoice_dashboard'),
            reverse('combined_invoice_dashboard'),
            reverse('reports_dashboard'),
            reverse('reports_export_csv'),
        ]

        for url in staff_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('combined_invoice_generate')).status_code, 403
        )

    def test_the_approval_gate_never_reads_the_seller_flag(self):
        """Regression guard: the gate's own logic must stay flag-blind."""
        from auctions.utils import needs_admin_approval

        pending_seller = make_seller('pendingflag')
        UserProfile.objects.filter(user=pending_seller).update(is_approved=False)
        pending_seller.refresh_from_db()

        self.assertTrue(needs_admin_approval(pending_seller))


class RegistrationHardeningStillOnTests(TestCase):
    """The four registration controls, checked as settings and as behaviour."""

    def setUp(self):
        cache.clear()

    def test_email_verification_is_mandatory(self):
        self.assertEqual(settings.ACCOUNT_EMAIL_VERIFICATION, 'mandatory')
        # Confirming an email must not log the user in around the approval gate.
        self.assertFalse(settings.ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION)

    def test_the_approval_and_mfa_adapter_is_the_one_in_use(self):
        self.assertEqual(settings.ACCOUNT_ADAPTER, 'auctions.adapters.AccountAdapter')

    def test_every_password_login_requires_a_code_and_passwordless_is_off(self):
        from allauth.account import app_settings

        self.assertEqual(app_settings.LOGIN_BY_CODE_REQUIRED, {'password'})
        self.assertFalse(app_settings.LOGIN_BY_CODE_ENABLED)

    def test_signup_is_rate_limited_per_ip(self):
        """Six posts from one address: the sixth is refused outright."""
        with patch('auctions.forms.verify_turnstile', return_value=True):
            statuses = []
            for i in range(6):
                response = self.client.post(
                    '/accounts/signup/',
                    {
                        'email': f'limit{i}@example.com',
                        'password1': PASSWORD,
                        'password2': PASSWORD,
                        'cf-turnstile-response': 'ok',
                    },
                    REMOTE_ADDR='198.51.100.77',
                )
                statuses.append(response.status_code)

        self.assertNotIn(403, statuses[:5])
        self.assertEqual(statuses[5], 403)

    def test_turnstile_failure_blocks_signup(self):
        with patch('auctions.forms.verify_turnstile', return_value=False):
            response = self.client.post(
                '/accounts/signup/',
                {
                    'email': 'bot@example.com',
                    'password1': PASSWORD,
                    'password2': PASSWORD,
                    'cf-turnstile-response': 'bad',
                },
                REMOTE_ADDR='198.51.100.78',
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='bot@example.com').exists())

    def test_the_turnstile_secret_is_read_from_the_environment_not_settings(self):
        """It must never be a settings constant that could end up in git."""
        import inspect

        from auctions import utils

        source = inspect.getsource(utils.verify_turnstile)
        self.assertIn("os.getenv('TURNSTILE_SECRET'", source)
        self.assertFalse(hasattr(settings, 'TURNSTILE_SECRET'))


class RateLimitCoverageTests(TestCase):
    """
    Every endpoint that was rate limited still is.

    Checked by the decorator's footprint on the class rather than by hammering
    each endpoint — django-ratelimit leaves the wrapped view reachable via
    __wrapped__, and a refactor that drops the decorator removes it.
    """

    def _is_rate_limited(self, view_class):
        return hasattr(view_class.post, '__wrapped__')

    def test_signup_login_bid_and_proxy_bid_are_all_limited(self):
        from auction_site.views import RateLimitedLoginView
        from auctions.views import (
            PlaceBidView,
            PlaceProxyBidView,
            RateLimitedSignupView,
        )

        for view in (RateLimitedSignupView, RateLimitedLoginView,
                     PlaceBidView, PlaceProxyBidView):
            with self.subTest(view=view.__name__):
                self.assertTrue(self._is_rate_limited(view))

    def test_the_limited_views_are_the_ones_the_urls_point_at(self):
        """A decorated class that nothing routes to protects nothing."""
        from django.urls import resolve

        from auction_site.views import RateLimitedLoginView
        from auctions.views import RateLimitedSignupView

        self.assertIs(
            resolve('/accounts/login/').func.view_class, RateLimitedLoginView
        )
        self.assertIs(
            resolve('/accounts/signup/').func.view_class, RateLimitedSignupView
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class InvoiceAuthorizationTests(TestCase):
    """Buyers see only their own bills; only staff run invoicing."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('rowan', first_name='Rowan')
        self.alice = User.objects.create_user('alice', 'alice@example.com', 'pw')
        self.bob = User.objects.create_user('bob', 'bob@example.com', 'pw')
        now = timezone.now()
        listing = AuctionListing.objects.create(
            title='Plant', category=self.category, seller=self.seller,
            start_price='10.00', starts_at=now - timedelta(days=2),
            ends_at=now + timedelta(days=2),
        )
        self.alices_invoice = Invoice.objects.create(
            listing=listing, buyer=self.alice, seller=self.seller,
            amount='20.00', shipping_fee='5.00',
        )

    def test_a_buyer_cannot_open_another_buyers_invoice(self):
        self.client.force_login(self.bob)

        response = self.client.get(
            reverse('invoice_detail', kwargs={'pk': self.alices_invoice.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_the_seller_on_an_invoice_cannot_open_it_either(self):
        """
        Selling the plant does not make the buyer's bill yours to read.

        The seller gets their own notification and their own dashboard; the
        invoice page carries the buyer's email and is the buyer's.
        """
        self.client.force_login(self.seller)

        response = self.client.get(
            reverse('invoice_detail', kwargs={'pk': self.alices_invoice.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_the_review_page_is_per_request_with_no_shared_state(self):
        """
        Two staff sessions reviewing two different invoices see their own.

        Guards against a module-level cache or context leak between requests.
        """
        staff = User.objects.create_user(
            'auditboss', 'auditboss@example.com', 'pw', is_staff=True,
        )
        Invoice.objects.create(
            listing=self.alices_invoice.listing, buyer=self.bob,
            seller=self.seller, amount='77.00', shipping_fee='0.00',
        )
        drafts = {d.buyer_id: d for d in generate_combined_invoices()}
        self.client.force_login(staff)

        alice_page = self.client.get(
            reverse('combined_invoice_review', args=[drafts[self.alice.pk].pk])
        ).content.decode()
        bob_page = self.client.get(
            reverse('combined_invoice_review', args=[drafts[self.bob.pk].pk])
        ).content.decode()

        self.assertIn('alice@example.com', alice_page)
        self.assertNotIn('bob@example.com', alice_page)
        self.assertIn('bob@example.com', bob_page)
        self.assertNotIn('alice@example.com', bob_page)


class PersonalDataExposureTests(TestCase):
    """Phone numbers, addresses and payment handles stay off public pages."""

    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Spiders')
        self.seller = make_seller(
            'rowan', first_name='Rowan', last_name='Fields',
            category=self.category,
        )
        UserProfile.objects.filter(user=self.seller).update(
            phone_number='555-0142-SELLER',
            address='12 Lily Lane, Somewhere',
            venmo_info='@rowan-fields-venmo',
        )
        self.buyer = User.objects.create_user(
            'wren', 'wren@example.com', 'pw', first_name='Wren',
        )
        UserProfile.objects.filter(user=self.buyer).update(
            phone_number='555-0199-BUYER', address='9 Buyer Road', country='US',
        )
        now = timezone.now()
        self.listing = AuctionListing.objects.create(
            title='Spider Ballerina', category=self.category, seller=self.seller,
            start_price='10.00', starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5), is_active=True,
        )

    PRIVATE = (
        '555-0142-SELLER', '12 Lily Lane', '@rowan-fields-venmo',
        '555-0199-BUYER', '9 Buyer Road',
    )

    def _assert_nothing_private(self, url):
        html = self.client.get(url).content.decode()
        for secret in self.PRIVATE:
            with self.subTest(url=url, secret=secret):
                self.assertNotIn(secret, html)

    def test_public_pages_carry_no_phone_address_or_payment_handle(self):
        for url in (
            reverse('home'),
            reverse('category_listings', kwargs={'slug': self.category.slug}),
            reverse('seller_listings', kwargs={'pk': self.seller.pk}),
            reverse('listing_detail', kwargs={'pk': self.listing.pk}),
            reverse('faq'),
            reverse('about_us'),
        ):
            self._assert_nothing_private(url)

    def test_the_seller_dashboard_shows_buyer_names_but_not_their_contact_details(self):
        Invoice.objects.create(
            listing=self.listing, buyer=self.buyer, seller=self.seller,
            amount='20.00', shipping_fee='5.00',
        )
        self.client.force_login(self.seller)

        html = self.client.get(reverse('seller_dashboard')).content.decode()

        self.assertIn('Wren', html)
        self.assertNotIn('wren@example.com', html)
        self.assertNotIn('555-0199-BUYER', html)
        self.assertNotIn('9 Buyer Road', html)

    def test_a_seller_sees_only_their_own_dashboard_data(self):
        other = make_seller('mallory', first_name='Mallory')
        now = timezone.now()
        theirs = AuctionListing.objects.create(
            title='Not Yours', category=self.category, seller=other,
            start_price='10.00', starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5), is_active=True,
        )
        Invoice.objects.create(
            listing=theirs, buyer=self.buyer, seller=other,
            amount='500.00', shipping_fee='0.00',
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse('seller_dashboard'))

        self.assertNotContains(response, 'Not Yours')
        self.assertEqual(response.context['total_revenue'], Decimal('0.00'))

    def test_a_member_cannot_reach_the_seller_dashboard(self):
        Subscription.objects.create(user=self.buyer, plan='monthly', status='active')
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('seller_dashboard'))

        self.assertRedirects(response, reverse('home'))


class ProductionSettingsTests(TestCase):
    """What the settings module does when the environment says nothing."""

    def test_debug_defaults_off(self):
        import importlib
        import os

        from auction_site import settings as settings_module

        with patch.dict(os.environ, {'DEBUG': ''}):
            importlib.reload(settings_module)
            try:
                self.assertFalse(settings_module.DEBUG)
            finally:
                importlib.reload(settings_module)

    def test_secrets_are_environment_sourced_not_literals(self):
        import inspect

        from auction_site import settings as settings_module

        source = inspect.getsource(settings_module)
        for name in ('SECRET_KEY', 'PAYPAL_CLIENT_SECRET', 'EMAIL_HOST_PASSWORD'):
            with self.subTest(name=name):
                self.assertRegex(
                    source, rf"{name}\s*=\s*os\.getenv\(", f'{name} not from env'
                )
