"""Tests for the manual admin-approval gate on new accounts.

The gate lives in AccountAdapter.pre_login(), so these tests drive real login
requests rather than calling the adapter directly — the point is that no session
is established, and only an end-to-end request proves that.
"""

import importlib

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.account.utils import perform_login
from allauth.core.context import request_context
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from auctions.admin import UserProfileAdmin, approve_selected_users, revoke_approval
from auctions.models import UserProfile

User = get_user_model()

LOGIN_URL = '/accounts/login/'
PASSWORD = 'daylily-passphrase-42'


def make_user(email, *, approved=False, staff=False, superuser=False):
    """Create a user with a verified email, bypassing the signup form."""
    user = User.objects.create_user(
        username=email.split('@')[0],
        email=email,
        password=PASSWORD,
        is_staff=staff or superuser,
        is_superuser=superuser,
    )
    EmailAddress.objects.create(
        user=user, email=email, verified=True, primary=True
    )
    # UserProfile is created by the post_save signal.
    profile = user.profile
    profile.is_approved = approved
    profile.save(update_fields=['is_approved'])
    return user


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ApprovalGateTests(TestCase):
    def setUp(self):
        cache.clear()

    def _login(self, email):
        return self.client.post(
            LOGIN_URL,
            {'login': email, 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.60',
            follow=True,
        )

    def test_unapproved_user_is_redirected_to_pending_approval(self):
        make_user('pending@example.com')

        response = self._login('pending@example.com')

        self.assertRedirects(response, reverse('account_pending_approval'))
        self.assertFalse(response.context['user'].is_authenticated)

    def test_unapproved_login_leaves_no_usable_session(self):
        """Not just the redirect — the session must not authenticate anything."""
        make_user('nosession@example.com')

        self._login('nosession@example.com')

        home = self.client.get('/', REMOTE_ADDR='198.51.100.60')
        self.assertFalse(home.context['user'].is_authenticated)

        profile_page = self.client.get('/profile/', REMOTE_ADDR='198.51.100.60')
        self.assertNotEqual(profile_page.status_code, 200)

    def test_unapproved_user_sees_an_explanation(self):
        make_user('explain@example.com')

        response = self._login('explain@example.com')

        self.assertContains(response, 'awaiting admin approval')

    def test_approved_user_can_log_in(self):
        make_user('approved@example.com', approved=True)

        response = self._login('approved@example.com')

        self.assertTrue(response.context['user'].is_authenticated)

    def test_staff_bypass_the_gate_while_unapproved(self):
        make_user('staff@example.com', staff=True, approved=False)

        response = self._login('staff@example.com')

        self.assertTrue(response.context['user'].is_authenticated)

    def test_superuser_bypasses_the_gate_while_unapproved(self):
        make_user('root@example.com', superuser=True, approved=False)

        response = self._login('root@example.com')

        self.assertTrue(response.context['user'].is_authenticated)

    def test_inactive_user_is_still_rejected(self):
        """The gate must not shadow allauth's own is_active check."""
        user = make_user('inactive@example.com', approved=True)
        user.is_active = False
        user.save(update_fields=['is_active'])

        response = self._login('inactive@example.com')

        self.assertFalse(response.context['user'].is_authenticated)

    def test_revoked_user_can_no_longer_log_in(self):
        user = make_user('revoked@example.com', approved=True)
        self.assertTrue(self._login('revoked@example.com').context['user'].is_authenticated)
        self.client.logout()

        profile = user.profile
        profile.is_approved = False
        profile.save(update_fields=['is_approved'])

        response = self._login('revoked@example.com')
        self.assertRedirects(response, reverse('account_pending_approval'))

    def test_user_without_a_profile_is_held(self):
        user = make_user('noprofile@example.com', approved=True)
        UserProfile.objects.filter(user=user).delete()

        response = self._login('noprofile@example.com')

        self.assertRedirects(response, reverse('account_pending_approval'))


class PendingApprovalPageTests(TestCase):
    def test_page_is_reachable_anonymously(self):
        """The user has no session by definition — the page must not require one."""
        response = self.client.get(reverse('account_pending_approval'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/pending_approval.html')
        self.assertContains(response, 'awaiting approval')

    def test_url_reverses_under_accounts_prefix(self):
        """Registered in auctions/urls.py but must fall through allauth's include."""
        self.assertEqual(
            reverse('account_pending_approval'), '/accounts/pending-approval/'
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ApprovalDefaultsTests(TestCase):
    def test_new_profiles_default_to_unapproved(self):
        user = User.objects.create_user(username='fresh', email='fresh@example.com')
        self.assertFalse(user.profile.is_approved)

    def test_migration_backfill_approves_pre_existing_profiles(self):
        """0013 grandfathers in accounts that predate the gate.

        Exercised against the migration's own function: by the time the test
        database is built the migration has already run, against zero rows.
        Without this backfill, deploying the gate locks out every current member.
        """
        migration = importlib.import_module(
            'auctions.migrations.0013_userprofile_is_approved'
        )
        old = make_user('legacy@example.com', approved=False)
        older = make_user('legacy2@example.com', approved=False)
        mail.outbox = []

        class FakeApps:
            def get_model(self, app_label, model_name):
                return UserProfile

        migration.approve_existing_accounts(FakeApps(), None)

        old.profile.refresh_from_db()
        older.profile.refresh_from_db()
        self.assertTrue(old.profile.is_approved)
        self.assertTrue(older.profile.is_approved)
        # A bulk update() emits no post_save, so grandfathering stays silent
        # rather than emailing the entire existing membership.
        self.assertEqual(mail.outbox, [])


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AdminApprovalActionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = AdminSite()
        self.model_admin = UserProfileAdmin(UserProfile, self.site)
        self.factory = RequestFactory()
        self.admin_user = make_user('boss@example.com', staff=True, superuser=True)

    def _request(self):
        request = self.factory.post('/admin/auctions/userprofile/')
        request.user = self.admin_user
        # Actions call message_user(), which needs a message store.
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def test_approve_action_approves_and_emails(self):
        user = make_user('needsapproval@example.com')
        mail.outbox = []

        approve_selected_users(
            self.model_admin,
            self._request(),
            UserProfile.objects.filter(user=user),
        )

        user.profile.refresh_from_db()
        self.assertTrue(user.profile.is_approved)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('has been approved', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['needsapproval@example.com'])

    def test_approved_user_can_log_in_after_the_action(self):
        user = make_user('thenlogin@example.com')

        approve_selected_users(
            self.model_admin,
            self._request(),
            UserProfile.objects.filter(user=user),
        )

        response = self.client.post(
            LOGIN_URL,
            {'login': 'thenlogin@example.com', 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.61',
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)

    def test_approve_action_is_idempotent(self):
        user = make_user('already@example.com', approved=True)
        mail.outbox = []

        approve_selected_users(
            self.model_admin,
            self._request(),
            UserProfile.objects.filter(user=user),
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_inline_edit_also_emails_the_user(self):
        """list_editable approval goes through save(), not the action."""
        user = make_user('inline@example.com')
        mail.outbox = []

        profile = user.profile
        profile.is_approved = True
        profile.save(update_fields=['is_approved'])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['inline@example.com'])

    def test_saving_an_already_approved_profile_does_not_re_email(self):
        user = make_user('quiet@example.com', approved=True)
        mail.outbox = []

        profile = user.profile
        profile.phone_number = '555-0100'
        profile.save()

        self.assertEqual(len(mail.outbox), 0)

    def test_staff_approval_does_not_email(self):
        user = make_user('newstaff@example.com', staff=True)
        mail.outbox = []

        profile = user.profile
        profile.is_approved = True
        profile.save(update_fields=['is_approved'])

        self.assertEqual(len(mail.outbox), 0)

    def test_revoke_action_unapproves(self):
        user = make_user('bye@example.com', approved=True)
        mail.outbox = []

        revoke_approval(
            self.model_admin,
            self._request(),
            UserProfile.objects.filter(user=user),
        )

        user.profile.refresh_from_db()
        self.assertFalse(user.profile.is_approved)
        # Revocation is deliberately silent.
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_exposes_approval_controls(self):
        self.assertIn('is_approved', self.model_admin.list_display)
        self.assertIn('is_approved', self.model_admin.list_editable)
        self.assertIn('is_approved', self.model_admin.list_filter)

    def test_changelist_shows_the_registered_email(self):
        """Usernames are auto-derived; the email is what identifies a person."""
        user = make_user('identify@example.com')
        self.assertIn('user_email', self.model_admin.list_display)
        self.assertEqual(
            self.model_admin.user_email(user.profile), 'identify@example.com'
        )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ADMIN_EMAIL='admin@asqdaylilies.com',
)
class EmailConfirmedNotificationTests(TestCase):
    """The email_confirmed receiver tells the admin there is work to do."""

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _confirm(self, user):
        address = EmailAddress.objects.get(user=user)
        address.verified = False
        address.save(update_fields=['verified'])
        key = EmailConfirmationHMAC(address).key
        return self.client.post(
            reverse('account_confirm_email', args=[key]),
            REMOTE_ADDR='198.51.100.62',
        )

    def test_admin_is_notified_when_a_new_user_confirms(self):
        user = make_user('newbie@example.com')

        self._confirm(user)

        notifications = [m for m in mail.outbox if 'pending approval' in m.subject]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].to, ['admin@asqdaylilies.com'])
        self.assertIn('newbie@example.com', notifications[0].subject)

    def test_staff_confirmation_does_not_notify(self):
        user = make_user('adminuser@example.com', staff=True)

        self._confirm(user)

        self.assertEqual(
            [m for m in mail.outbox if 'pending approval' in m.subject], []
        )

    def test_superuser_confirmation_does_not_notify(self):
        user = make_user('rootuser@example.com', superuser=True)

        self._confirm(user)

        self.assertEqual(
            [m for m in mail.outbox if 'pending approval' in m.subject], []
        )

    def test_already_approved_user_confirmation_does_not_notify(self):
        user = make_user('fasttrack@example.com', approved=True)

        self._confirm(user)

        self.assertEqual(
            [m for m in mail.outbox if 'pending approval' in m.subject], []
        )

    @override_settings(ADMIN_EMAIL='')
    def test_no_admin_email_configured_is_a_no_op(self):
        user = make_user('quietsignup@example.com')

        self._confirm(user)

        self.assertEqual(
            [m for m in mail.outbox if 'pending approval' in m.subject], []
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class NonPasswordLoginPathTests(TestCase):
    """
    The gate sits in pre_login(), which every login path routes through.

    Social login (allauth.socialaccount.internal.flows.login._login) reaches it
    via account.utils.perform_login with email_verification='none' — exactly the
    call exercised here. Testing the shared entry point rather than driving an
    OAuth round-trip keeps this independent of provider plumbing while still
    covering the path a Google/Facebook login takes.
    """

    def _login(self, user):
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        MessageMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        # Stand in for AccountMiddleware, which normally publishes the request
        # so the adapter can reach it.
        with request_context(request):
            return perform_login(request, user, email_verification='none')

    def test_unapproved_user_is_gated(self):
        user = make_user('social@example.com')

        response = self._login(user)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('account_pending_approval'))

    def test_approved_user_is_let_through(self):
        user = make_user('socialok@example.com', approved=True)

        response = self._login(user)

        self.assertNotEqual(response.url, reverse('account_pending_approval'))

    def test_staff_are_let_through(self):
        user = make_user('socialstaff@example.com', staff=True)

        response = self._login(user)

        self.assertNotEqual(response.url, reverse('account_pending_approval'))
