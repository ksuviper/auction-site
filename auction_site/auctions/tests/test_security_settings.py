"""Tests for the Login & Security page, and for allauth's pages rendering at all.

The render tests exist because of a real failure: the project shadowed
allauth's account/base_confirm_code.html with a wrapper that dropped
{% block content %}, so the login-code screen came out as a heading with no
form — a 200 response nobody could log in through. A test-client check on the
redirect path passes straight through that, so these assert on markup.
"""

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from auctions.models import UserProfile

User = get_user_model()

SECURITY_URL = '/account/security/'
TOGGLE_URL = '/account/security/toggle-email-code/'
PASSWORD = 'daylily-passphrase-42'


def make_user(email, *, staff=False, email_codes=True):
    user = User.objects.create_user(
        username=email.split('@')[0],
        email=email,
        password=PASSWORD,
        is_staff=staff,
        is_superuser=staff,
    )
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={'verified': True, 'primary': True}
    )
    UserProfile.objects.filter(user=user).update(
        is_approved=True, email_login_code_enabled=email_codes
    )
    return user


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SecuritySettingsPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_required(self):
        response = self.client.get(SECURITY_URL)
        self.assertNotEqual(response.status_code, 200)

    def test_shows_email_code_on(self):
        self.client.force_login(make_user('on@example.com'))

        response = self.client.get(SECURITY_URL)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'auctions/security_settings.html')
        self.assertContains(response, 'Turn off login codes')

    def test_shows_email_code_off(self):
        self.client.force_login(make_user('off@example.com', email_codes=False))

        response = self.client.get(SECURITY_URL)

        self.assertContains(response, 'Turn on login codes')

    def test_staff_see_a_locked_note_and_no_toggle(self):
        self.client.force_login(make_user('boss@example.com', staff=True))

        response = self.client.get(SECURITY_URL)

        self.assertTrue(response.context['email_code_locked'])
        self.assertContains(response, 'cannot disable it')
        self.assertNotContains(response, 'Turn off login codes')

    def test_offers_totp_setup_when_inactive(self):
        self.client.force_login(make_user('nototp@example.com'))

        response = self.client.get(SECURITY_URL)

        self.assertFalse(response.context['totp_active'])
        self.assertContains(response, reverse('mfa_activate_totp'))
        self.assertContains(response, 'instead of an emailed code')

    def test_shows_totp_as_enabled_when_active(self):
        user = make_user('hastotp@example.com')
        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={'secret': 'JBSWY3DPEHPK3PXP'}
        )
        self.client.force_login(user)

        response = self.client.get(SECURITY_URL)

        self.assertTrue(response.context['totp_active'])
        self.assertContains(response, 'Enabled')
        self.assertContains(response, reverse('mfa_index'))

    def test_explains_that_totp_supersedes_the_email_code(self):
        user = make_user('supersede@example.com')
        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={'secret': 'JBSWY3DPEHPK3PXP'}
        )
        self.client.force_login(user)

        response = self.client.get(SECURITY_URL)

        self.assertContains(response, 'instead of emailing one')
        self.assertNotContains(response, 'Turn off login codes')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ToggleEmailCodeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_toggle_off_then_on(self):
        user = make_user('flip@example.com')
        self.client.force_login(user)

        self.client.post(TOGGLE_URL)
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.email_login_code_enabled)

        self.client.post(TOGGLE_URL)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.email_login_code_enabled)

    def test_toggle_redirects_back_with_a_message(self):
        self.client.force_login(make_user('msg@example.com'))

        response = self.client.post(TOGGLE_URL, follow=True)

        self.assertRedirects(response, SECURITY_URL)
        self.assertContains(response, 'Login codes are off')

    def test_get_is_not_allowed(self):
        self.client.force_login(make_user('getonly@example.com'))

        response = self.client.get(TOGGLE_URL)

        self.assertEqual(response.status_code, 405)

    def test_staff_are_refused_server_side(self):
        """The page hides the control; the view must refuse it regardless."""
        user = make_user('staffflip@example.com', staff=True)
        self.client.force_login(user)

        response = self.client.post(TOGGLE_URL)

        self.assertEqual(response.status_code, 403)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.email_login_code_enabled)

    def test_anonymous_cannot_toggle(self):
        response = self.client.post(TOGGLE_URL)
        self.assertNotEqual(response.status_code, 200)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AllauthPageRenderTests(TestCase):
    """
    Every allauth page in these flows must render its form, not just return 200.

    Overriding an allauth base template is how the login-code screen ended up
    blank; these catch the same mistake on the other pages.
    """

    def setUp(self):
        cache.clear()

    def _start_login(self, user):
        """Get to the mid-login code screen."""
        self.client.post(
            '/accounts/login/',
            {'login': user.email, 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.95',
        )

    def _login_recently(self, user):
        """
        Log in with a fresh authentication on record.

        The MFA management views are @reauthentication_required, and force_login
        leaves no authentication record, so without this they answer 302 to the
        reauthenticate page rather than rendering.
        """
        import time

        from allauth.account.internal.flows.login import (
            AUTHENTICATION_METHODS_SESSION_KEY,
        )

        self.client.force_login(user)
        session = self.client.session
        session[AUTHENTICATION_METHODS_SESSION_KEY] = [
            {'method': 'password', 'at': time.time()}
        ]
        session.save()

    def test_login_code_screen_renders_a_usable_form(self):
        user = make_user('codescreen@example.com')
        self._start_login(user)

        response = self.client.get(
            '/accounts/login/code/confirm/', REMOTE_ADDR='198.51.100.95'
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('<form', html)
        self.assertIn('name="code"', html)
        self.assertIn('type="submit"', html)

    def test_login_code_screen_is_inside_the_site_chrome(self):
        user = make_user('branded@example.com')
        self._start_login(user)

        response = self.client.get(
            '/accounts/login/code/confirm/', REMOTE_ADDR='198.51.100.95'
        )

        # base.html's landmark and the site footer, i.e. not allauth's bare doc.
        self.assertContains(response, 'id="main-content"')
        self.assertContains(response, 'ASQ Daylily')
        self.assertContains(response, 'btn btn-primary')

    def test_mfa_challenge_screen_renders_a_usable_form(self):
        user = make_user('mfascreen@example.com')
        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={'secret': 'JBSWY3DPEHPK3PXP'}
        )
        self._start_login(user)

        response = self.client.get(
            reverse('mfa_authenticate'), REMOTE_ADDR='198.51.100.95'
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('<form', html)
        self.assertIn('name="code"', html)
        self.assertIn('id="main-content"', html)

    def test_mfa_index_renders(self):
        self.client.force_login(make_user('mfaindex@example.com'))

        response = self.client.get(reverse('mfa_index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="main-content"')

    def test_totp_activation_screen_renders_the_qr_and_form(self):
        self._login_recently(make_user('totpsetup@example.com'))

        response = self.client.get(reverse('mfa_activate_totp'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('<form', html)
        self.assertIn('name="code"', html)
        # allauth renders the QR code as an inline SVG data URI.
        self.assertIn('svg', html.lower())
        self.assertIn('id="main-content"', html)

    def test_reauthenticate_screen_renders_a_usable_form(self):
        """Gates MFA management — blank here means TOTP can never be set up."""
        self.client.force_login(make_user('reauth@example.com'))

        response = self.client.get(reverse('account_reauthenticate'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('<form', html)
        self.assertIn('name="password"', html)
        self.assertIn('type="submit"', html)

    def test_recovery_codes_screen_renders(self):
        user = make_user('reccodes@example.com')
        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={'secret': 'JBSWY3DPEHPK3PXP'}
        )
        self._login_recently(user)

        response = self.client.get(reverse('mfa_generate_recovery_codes'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<form')
        self.assertContains(response, 'id="main-content"')

    def test_login_code_email_is_recognisable(self):
        from django.core import mail

        user = make_user('subject@example.com')
        mail.outbox = []
        self._start_login(user)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('ASQ Daylily Auctions login code', message.subject)
        # The code itself is in the subject, readable from an inbox list.
        self.assertRegex(message.subject, r'login code: [A-Z0-9]{6,}')
        self.assertIn('ASQ Daylily Auction Group', message.body)
