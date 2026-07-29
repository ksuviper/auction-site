"""Tests for the registration hardening: rate limiting, Turnstile, verification.

The signup rate limit is keyed on the client IP and counted in the cache, which
outlives an individual test method, so every test class here clears the cache in
setUp() and — where the assertion is not about the limit itself — posts from a
distinct fake IP via REMOTE_ADDR.
"""

from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()

SIGNUP_URL = '/accounts/signup/'


def signup_data(email, first='Jane', last='Doe'):
    return {
        'first_name': first,
        'last_name': last,
        'email': email,
        'password1': 'daylily-passphrase-42',
        'password2': 'daylily-passphrase-42',
        'turnstile_token': 'dummy-token',
    }


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SignupUrlOverrideTests(TestCase):
    """The project's rate-limited view must win over allauth's own signup URL."""

    def setUp(self):
        cache.clear()

    def test_account_signup_url_resolves_to_rate_limited_view(self):
        from auctions.views import RateLimitedSignupView

        match = self.client.get(
            SIGNUP_URL, REMOTE_ADDR='198.51.100.10'
        ).resolver_match
        self.assertEqual(match.func.view_class, RateLimitedSignupView)
        self.assertEqual(reverse('account_signup'), SIGNUP_URL)

    def test_signup_page_exposes_the_turnstile_site_key(self):
        with override_settings(TURNSTILE_SITE_KEY='0xTESTSITEKEY'):
            response = self.client.get(SIGNUP_URL, REMOTE_ADDR='198.51.100.11')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['turnstile_site_key'], '0xTESTSITEKEY')
        self.assertContains(response, '0xTESTSITEKEY')
        self.assertContains(response, 'cf-turnstile')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SignupRateLimitTests(TestCase):
    """5 signup POSTs per hour per IP; the 6th is refused outright."""

    def setUp(self):
        cache.clear()

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_sixth_signup_attempt_from_same_ip_is_blocked(self, mock_verify):
        for i in range(5):
            response = self.client.post(
                SIGNUP_URL,
                signup_data(f'signup{i}@example.com'),
                REMOTE_ADDR='198.51.100.20',
            )
            self.assertIn(
                response.status_code,
                (200, 302),
                msg=f'attempt {i + 1} should not have been rate limited',
            )

        blocked = self.client.post(
            SIGNUP_URL,
            signup_data('signup5@example.com'),
            REMOTE_ADDR='198.51.100.20',
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(User.objects.filter(email='signup5@example.com').exists())

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_limit_is_per_ip(self, mock_verify):
        for i in range(5):
            self.client.post(
                SIGNUP_URL,
                signup_data(f'first{i}@example.com'),
                REMOTE_ADDR='198.51.100.21',
            )

        # A different address still has its own budget.
        response = self.client.post(
            SIGNUP_URL,
            signup_data('other@example.com'),
            REMOTE_ADDR='198.51.100.22',
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(User.objects.filter(email='other@example.com').exists())

    def test_get_requests_are_not_rate_limited(self):
        for _ in range(8):
            response = self.client.get(SIGNUP_URL, REMOTE_ADDR='198.51.100.23')
            self.assertEqual(response.status_code, 200)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TurnstileSignupTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch('auctions.forms.verify_turnstile', return_value=False)
    def test_failed_turnstile_rejects_signup(self, mock_verify):
        response = self.client.post(
            SIGNUP_URL,
            signup_data('bot@example.com'),
            REMOTE_ADDR='198.51.100.30',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please complete the verification check')
        self.assertFalse(User.objects.filter(email='bot@example.com').exists())
        self.assertTrue(mock_verify.called)

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_passed_turnstile_creates_the_user(self, mock_verify):
        response = self.client.post(
            SIGNUP_URL,
            signup_data('human@example.com', first='Ada', last='Lovelace'),
            REMOTE_ADDR='198.51.100.31',
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='human@example.com')
        self.assertEqual(user.first_name, 'Ada')
        self.assertEqual(user.last_name, 'Lovelace')

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_token_and_client_ip_are_handed_to_the_verifier(self, mock_verify):
        self.client.post(
            SIGNUP_URL,
            signup_data('ipcheck@example.com'),
            REMOTE_ADDR='198.51.100.32',
        )
        mock_verify.assert_called_with('dummy-token', '198.51.100.32')

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_missing_token_still_reaches_the_verifier(self, mock_verify):
        """An empty token must be rejected by verify_turnstile, not skipped."""
        data = signup_data('notoken@example.com')
        data.pop('turnstile_token')
        self.client.post(SIGNUP_URL, data, REMOTE_ADDR='198.51.100.33')
        mock_verify.assert_called_with('', '198.51.100.33')


class VerifyTurnstileTests(TestCase):
    """Unit tests for the helper itself — no signup view involved."""

    @override_settings(DEBUG=False)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': 'test-secret'})
    @patch('auctions.utils.requests.post')
    def test_success_response_passes(self, mock_post):
        mock_post.return_value.json.return_value = {'success': True}
        mock_post.return_value.raise_for_status.return_value = None

        from auctions.utils import verify_turnstile

        self.assertTrue(verify_turnstile('token', '203.0.113.1'))
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs['data'],
            {
                'secret': 'test-secret',
                'response': 'token',
                'remoteip': '203.0.113.1',
            },
        )

    @override_settings(DEBUG=False)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': 'test-secret'})
    @patch('auctions.utils.requests.post')
    def test_unsuccessful_response_fails(self, mock_post):
        mock_post.return_value.json.return_value = {
            'success': False,
            'error-codes': ['invalid-input-response'],
        }
        mock_post.return_value.raise_for_status.return_value = None

        from auctions.utils import verify_turnstile

        self.assertFalse(verify_turnstile('token', '203.0.113.1'))

    @override_settings(DEBUG=False)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': 'test-secret'})
    @patch('auctions.utils.requests.post', side_effect=OSError('boom'))
    def test_unreachable_endpoint_fails_closed(self, mock_post):
        from auctions.utils import verify_turnstile

        self.assertFalse(verify_turnstile('token', '203.0.113.1'))

    @override_settings(DEBUG=False)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': 'test-secret'})
    @patch('auctions.utils.requests.post')
    def test_empty_token_fails_without_calling_cloudflare(self, mock_post):
        from auctions.utils import verify_turnstile

        self.assertFalse(verify_turnstile('', '203.0.113.1'))
        self.assertFalse(mock_post.called)

    @override_settings(DEBUG=False)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': ''})
    def test_missing_secret_fails_closed_when_debug_is_off(self):
        from auctions.utils import verify_turnstile

        self.assertFalse(verify_turnstile('token', '203.0.113.1'))

    @override_settings(DEBUG=True)
    @patch.dict('os.environ', {'TURNSTILE_SECRET': ''})
    def test_missing_secret_is_skipped_in_local_development(self):
        from auctions.utils import verify_turnstile

        self.assertTrue(verify_turnstile('token', '203.0.113.1'))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MandatoryEmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_signup_sends_a_verification_email_and_does_not_log_in(self, mock_verify):
        response = self.client.post(
            SIGNUP_URL,
            signup_data('unverified@example.com'),
            REMOTE_ADDR='198.51.100.40',
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(response.context['user'].is_authenticated)

        address = EmailAddress.objects.get(email='unverified@example.com')
        self.assertFalse(address.verified)

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_unverified_user_cannot_log_in(self, mock_verify):
        self.client.post(
            SIGNUP_URL,
            signup_data('cantlogin@example.com'),
            REMOTE_ADDR='198.51.100.41',
        )
        self.client.logout()

        response = self.client.post(
            '/accounts/login/',
            {'login': 'cantlogin@example.com', 'password': 'daylily-passphrase-42'},
            REMOTE_ADDR='198.51.100.41',
            follow=True,
        )

        self.assertFalse(response.context['user'].is_authenticated)

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_verified_user_can_log_in(self, mock_verify):
        self.client.post(
            SIGNUP_URL,
            signup_data('verified@example.com'),
            REMOTE_ADDR='198.51.100.42',
        )
        self.client.logout()

        address = EmailAddress.objects.get(email='verified@example.com')
        address.verified = True
        address.save(update_fields=['verified'])

        response = self.client.post(
            '/accounts/login/',
            {'login': 'verified@example.com', 'password': 'daylily-passphrase-42'},
            REMOTE_ADDR='198.51.100.42',
            follow=True,
        )

        self.assertTrue(response.context['user'].is_authenticated)

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_confirming_in_the_same_browser_lands_on_profile_completion(
        self, mock_verify
    ):
        """The adapter should send a freshly confirmed user to profile setup."""
        self.client.post(
            SIGNUP_URL,
            signup_data('confirmer@example.com'),
            REMOTE_ADDR='198.51.100.43',
        )

        confirmation = self._confirmation_for('confirmer@example.com')
        response = self.client.post(
            reverse('account_confirm_email', args=[confirmation.key]),
            REMOTE_ADDR='198.51.100.43',
        )

        self.assertRedirects(
            response,
            reverse('profile_edit') + '?next=/',
            fetch_redirect_response=False,
        )
        self.assertTrue(
            EmailAddress.objects.get(email='confirmer@example.com').verified
        )

    @patch('auctions.forms.verify_turnstile', return_value=True)
    def test_confirming_from_another_browser_lands_on_login(self, mock_verify):
        """No sign-up session to resume (e.g. link opened on a phone)."""
        self.client.post(
            SIGNUP_URL,
            signup_data('elsewhere@example.com'),
            REMOTE_ADDR='198.51.100.44',
        )

        confirmation = self._confirmation_for('elsewhere@example.com')
        other_browser = self.client_class()
        response = other_browser.post(
            reverse('account_confirm_email', args=[confirmation.key]),
            REMOTE_ADDR='203.0.113.99',
        )

        self.assertRedirects(
            response, reverse('account_login'), fetch_redirect_response=False
        )
        self.assertTrue(
            EmailAddress.objects.get(email='elsewhere@example.com').verified
        )

    def _confirmation_for(self, email):
        from allauth.account.models import EmailConfirmationHMAC

        return EmailConfirmationHMAC(EmailAddress.objects.get(email=email))
