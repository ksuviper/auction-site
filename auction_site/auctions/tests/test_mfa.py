"""Tests for the two-factor policy.

  * Every password login needs an emailed one-time code by default.
  * Staff cannot opt out — enforced in the adapter, not just hidden in the UI.
  * Anyone with an authenticator app is challenged by that instead; the two
    prompts must never stack.
  * Social logins are untouched.
"""

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from auctions.models import UserProfile

User = get_user_model()

LOGIN_URL = '/accounts/login/'
CODE_URL = '/accounts/login/code/confirm/'
PASSWORD = 'daylily-passphrase-42'


def make_user(email, *, staff=False, superuser=False, email_codes=True):
    """An approved, verified account — so only the 2FA gate is under test."""
    user = User.objects.create_user(
        username=email.split('@')[0],
        email=email,
        password=PASSWORD,
        is_staff=staff or superuser,
        is_superuser=superuser,
    )
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={'verified': True, 'primary': True}
    )
    # update() rather than save(): flipping is_approved through save() fires the
    # "your account has been approved" email, and these tests assert on exactly
    # which mail a login produces.
    UserProfile.objects.filter(user=user).update(
        is_approved=True, email_login_code_enabled=email_codes
    )
    return user


def add_totp(user):
    """Attach an active TOTP authenticator without going through the UI."""
    return Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.TOTP,
        data={'secret': 'JBSWY3DPEHPK3PXP'},
    )


def logged_in(client):
    return client.session.get('_auth_user_id') is not None


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailCodeAtLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _login(self, email, ip='198.51.100.90'):
        return self.client.post(
            LOGIN_URL,
            {'login': email, 'password': PASSWORD},
            REMOTE_ADDR=ip,
            follow=True,
        )

    def test_default_login_is_interrupted_by_a_code(self):
        make_user('coded@example.com')

        response = self._login('coded@example.com')

        self.assertFalse(logged_in(self.client))
        self.assertEqual(response.request['PATH_INFO'], CODE_URL)

    def test_the_code_is_emailed(self):
        make_user('mailed@example.com')

        self._login('mailed@example.com')

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['mailed@example.com'])

    def test_entering_the_code_completes_the_login(self):
        make_user('finisher@example.com')
        self._login('finisher@example.com')

        code = self._sent_code()
        self.client.post(CODE_URL, {'code': code}, REMOTE_ADDR='198.51.100.90')

        self.assertTrue(logged_in(self.client))

    def test_a_wrong_code_does_not_log_in(self):
        make_user('wrong@example.com')
        self._login('wrong@example.com')

        self.client.post(CODE_URL, {'code': '000000'}, REMOTE_ADDR='198.51.100.90')

        self.assertFalse(logged_in(self.client))

    def test_user_who_opted_out_logs_in_with_password_only(self):
        make_user('nocode@example.com', email_codes=False)

        self._login('nocode@example.com')

        self.assertTrue(logged_in(self.client))
        self.assertEqual(mail.outbox, [])

    def test_staff_cannot_opt_out_even_by_direct_db_write(self):
        """Server-side enforcement: the profile flag is ignored for staff."""
        user = make_user('boss@example.com', staff=True, email_codes=False)
        UserProfile.objects.filter(user=user).update(email_login_code_enabled=False)

        response = self._login('boss@example.com')

        self.assertFalse(logged_in(self.client))
        self.assertEqual(response.request['PATH_INFO'], CODE_URL)
        self.assertEqual(len(mail.outbox), 1)

    def test_superuser_cannot_opt_out_either(self):
        user = make_user('root@example.com', superuser=True, email_codes=False)
        UserProfile.objects.filter(user=user).update(email_login_code_enabled=False)

        self._login('root@example.com')

        self.assertFalse(logged_in(self.client))

    def test_missing_profile_falls_back_to_requiring_a_code(self):
        user = make_user('noprofile@example.com')
        UserProfile.objects.filter(user=user).delete()

        self._login('noprofile@example.com')

        self.assertFalse(logged_in(self.client))

    def _sent_code(self):
        """
        Pull the one-time code out of the email allauth just sent.

        The codes are uppercase alphanumeric (e.g. 'KQWFXP'), not digits, and
        sit on a line of their own.
        """
        body = mail.outbox[-1].body
        for line in body.splitlines():
            token = line.strip()
            if len(token) >= 6 and token.isalnum() and token.upper() == token:
                return token
        raise AssertionError(f'No code found in email body:\n{body}')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TotpReplacesEmailCodeTests(TestCase):
    """An authenticator app must replace the email code, never stack with it."""

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _login(self, email):
        return self.client.post(
            LOGIN_URL,
            {'login': email, 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.91',
            follow=True,
        )

    def test_totp_user_is_challenged_by_the_app(self):
        user = make_user('totp@example.com')
        add_totp(user)

        response = self._login('totp@example.com')

        self.assertFalse(logged_in(self.client))
        self.assertEqual(response.request['PATH_INFO'], reverse('mfa_authenticate'))

    def test_totp_user_gets_no_email_code(self):
        user = make_user('totpnomail@example.com')
        add_totp(user)

        self._login('totpnomail@example.com')

        self.assertEqual(mail.outbox, [])

    def test_totp_wins_even_for_staff(self):
        """Staff cannot disable 2FA, but TOTP is a valid stronger substitute."""
        user = make_user('totpboss@example.com', staff=True)
        add_totp(user)

        response = self._login('totpboss@example.com')

        self.assertEqual(response.request['PATH_INFO'], reverse('mfa_authenticate'))
        self.assertEqual(mail.outbox, [])

    def test_totp_wins_even_when_the_user_opted_out_of_email_codes(self):
        """Opting out of email codes must not opt out of the app challenge."""
        user = make_user('totpoptout@example.com', email_codes=False)
        add_totp(user)

        response = self._login('totpoptout@example.com')

        self.assertFalse(logged_in(self.client))
        self.assertEqual(response.request['PATH_INFO'], reverse('mfa_authenticate'))

    def test_recovery_codes_alone_do_not_replace_the_email_code(self):
        """Recovery codes are not a login challenge on their own."""
        user = make_user('recovery@example.com')
        Authenticator.objects.create(
            user=user,
            type=Authenticator.Type.RECOVERY_CODES,
            data={'migrated_codes': [], 'seed': 'x'},
        )

        response = self._login('recovery@example.com')

        self.assertEqual(response.request['PATH_INFO'], CODE_URL)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SocialLoginUntouchedTests(TestCase):
    """ACCOUNT_LOGIN_BY_CODE_REQUIRED = {'password'} keeps social logins clear."""

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _social_login(self, user):
        """
        Mirror allauth.socialaccount.internal.flows.login._login.

        Recording the authentication as 'socialaccount' is the step that matters:
        ACCOUNT_LOGIN_BY_CODE_REQUIRED = {'password'} is matched against that
        method. Skipping it leaves no record at all, which allauth treats as
        "unknown, require the code" — fail-secure, but not what a real social
        login does.
        """
        from allauth.account.utils import perform_login
        from allauth.core.context import request_context
        from allauth.socialaccount.internal.flows.login import record_authentication
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        MessageMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()

        account = SocialAccount.objects.create(
            user=user, provider='google', uid=f'uid-{user.pk}'
        )
        sociallogin = SocialLogin(user=user, account=account)

        with request_context(request):
            record_authentication(request, sociallogin)
            return perform_login(request, user, email_verification='none')

    def test_social_login_needs_no_email_code(self):
        user = make_user('social@example.com')

        response = self._social_login(user)

        self.assertNotIn('code', response.url)
        self.assertEqual(mail.outbox, [])

    def test_social_login_of_a_staff_user_needs_no_email_code(self):
        user = make_user('socialboss@example.com', staff=True)

        response = self._social_login(user)

        self.assertNotIn('code', response.url)
        self.assertEqual(mail.outbox, [])


class AdapterHookContractTests(TestCase):
    """
    Guards the assumptions this policy is built on.

    If an allauth upgrade changes the hook signature or the stage ordering, the
    email code and the app challenge could silently start stacking — or worse,
    stop being required at all. These fail loudly instead.
    """

    def test_hook_takes_a_login_object(self):
        import inspect

        from allauth.account.adapter import DefaultAccountAdapter

        params = list(
            inspect.signature(
                DefaultAccountAdapter.is_login_by_code_required
            ).parameters
        )
        self.assertEqual(params, ['self', 'login'])

    def test_login_by_code_stage_still_runs_before_the_mfa_stage(self):
        from allauth.account.adapter import get_adapter

        stages = get_adapter().get_login_stages()
        self.assertLess(
            stages.index('allauth.account.stages.LoginByCodeStage'),
            stages.index('allauth.mfa.stages.AuthenticateStage'),
            'If the MFA stage ran first, the adapter override would be '
            'unnecessary — and the ordering assumption in its docstring wrong.',
        )

    def test_passwordless_login_by_code_stays_disabled(self):
        """LOGIN_BY_CODE_ENABLED would add a password-free sign-in route."""
        from allauth.account import app_settings

        self.assertFalse(app_settings.LOGIN_BY_CODE_ENABLED)
        self.assertEqual(app_settings.LOGIN_BY_CODE_REQUIRED, {'password'})
