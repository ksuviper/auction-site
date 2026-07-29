"""Regression tests for the mandatory-verification lockout.

Turning on ACCOUNT_EMAIL_VERIFICATION = 'mandatory' blocks any account without a
verified EmailAddress row. Two groups had no way out:

  * createsuperuser accounts, which get no EmailAddress row at all; and
  * members who signed up while verification was optional (verified=False).

Migration 0014 repairs accounts that already existed, a post_save receiver keeps
new staff accounts out of the trap, and `manage.py unlock_account` is the manual
escape hatch.
"""

import importlib
from io import StringIO

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from auctions.models import UserProfile

User = get_user_model()

LOGIN_URL = '/accounts/login/'
PASSWORD = 'daylily-passphrase-42'


def logged_in(client):
    return client.session.get('_auth_user_id') is not None


class HistoricalApps:
    """Stand-in for the migration's `apps` registry, using current models."""

    def get_model(self, *label):
        joined = '.'.join(label) if len(label) > 1 else label[0]
        if joined.lower() in {'auth.user', 'account.emailaddress'}:
            return {
                'auth.user': User,
                'account.emailaddress': EmailAddress,
            }[joined.lower()]
        raise LookupError(joined)


def run_migration_0014():
    module = importlib.import_module(
        'auctions.migrations.0014_verify_pre_existing_emails'
    )
    module.verify_pre_existing_emails(HistoricalApps(), None)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SuperuserLockoutTests(TestCase):
    """createsuperuser must not produce an account that cannot log in."""

    def setUp(self):
        cache.clear()

    def test_createsuperuser_now_gets_a_verified_email(self):
        call_command(
            'createsuperuser',
            interactive=False,
            username='boss',
            email='boss@example.com',
            stdout=StringIO(),
        )
        user = User.objects.get(username='boss')
        user.set_password(PASSWORD)
        user.save()

        address = EmailAddress.objects.get(user=user)
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)

    def test_new_superuser_can_log_in(self):
        user = User.objects.create_superuser(
            username='root', email='root@example.com', password=PASSWORD
        )

        response = self.client.post(
            LOGIN_URL,
            {'login': 'root@example.com', 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.80',
            follow=True,
        )

        self.assertTrue(logged_in(self.client))
        self.assertTrue(response.context['user'].is_authenticated)
        # Staff also bypass the approval gate, even though nothing approved them.
        self.assertFalse(user.profile.is_approved)

    def test_staff_added_in_the_admin_also_gets_verified(self):
        user = User.objects.create_user(
            username='helper', email='helper@example.com',
            password=PASSWORD, is_staff=True,
        )
        self.assertTrue(EmailAddress.objects.get(user=user).verified)

    def test_normal_signups_are_not_auto_verified(self):
        """The receiver must not hand out verification to ordinary users."""
        user = User.objects.create_user(
            username='regular', email='regular@example.com', password=PASSWORD
        )
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

    def test_staff_without_an_email_is_skipped(self):
        user = User.objects.create_superuser(
            username='noemail', email='', password=PASSWORD
        )
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

    def test_address_already_verified_elsewhere_is_not_stolen(self):
        owner = User.objects.create_user(
            username='owner', email='shared@example.com', password=PASSWORD
        )
        EmailAddress.objects.create(
            user=owner, email='shared@example.com', verified=True, primary=True
        )

        newcomer = User.objects.create_superuser(
            username='newboss', email='shared@example.com', password=PASSWORD
        )

        self.assertFalse(EmailAddress.objects.filter(user=newcomer).exists())
        self.assertEqual(
            EmailAddress.objects.filter(
                email='shared@example.com', verified=True
            ).count(),
            1,
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class Migration0014Tests(TestCase):
    """The backfill has to reach both locked-out groups."""

    def setUp(self):
        cache.clear()

    def _member(self, username, email, *, verified=False, with_row=True):
        user = User.objects.create_user(
            username=username, email=email, password=PASSWORD
        )
        if with_row:
            EmailAddress.objects.create(
                user=user, email=email, verified=verified, primary=True
            )
        profile = user.profile
        profile.is_approved = True
        profile.save(update_fields=['is_approved'])
        return user

    def test_unconfirmed_member_is_grandfathered(self):
        member = self._member('legacy', 'legacy@example.com')

        run_migration_0014()

        self.assertTrue(EmailAddress.objects.get(user=member).verified)

    def test_member_can_log_in_after_the_backfill(self):
        self._member('canlogin', 'canlogin@example.com')

        run_migration_0014()

        self.client.post(
            LOGIN_URL,
            {'login': 'canlogin@example.com', 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.81',
        )
        self.assertTrue(logged_in(self.client))

    def test_user_with_no_email_row_gets_one(self):
        """The createsuperuser shape, for databases migrated before the fix."""
        orphan = self._member('orphan', 'orphan@example.com', with_row=False)
        EmailAddress.objects.filter(user=orphan).delete()

        run_migration_0014()

        address = EmailAddress.objects.get(user=orphan)
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)

    def test_users_without_an_address_are_skipped(self):
        blank = User.objects.create_user(username='blank', email='', password=PASSWORD)

        run_migration_0014()

        self.assertFalse(EmailAddress.objects.filter(user=blank).exists())

    def test_contested_address_goes_to_the_oldest_account(self):
        """unique_verified_email allows exactly one verified owner."""
        first = self._member('first', 'dupe@example.com')
        second = User.objects.create_user(
            username='second', email='dupe@example.com', password=PASSWORD
        )
        EmailAddress.objects.create(
            user=second, email='dupe@example.com', verified=False, primary=True
        )

        run_migration_0014()

        self.assertTrue(EmailAddress.objects.get(user=first).verified)
        self.assertFalse(EmailAddress.objects.get(user=second).verified)

    def test_already_verified_rows_are_left_alone(self):
        member = self._member('fine', 'fine@example.com', verified=True)

        run_migration_0014()

        self.assertTrue(EmailAddress.objects.get(user=member).verified)

    def test_backfill_does_not_touch_approval(self):
        """Verification and approval are separate gates."""
        member = User.objects.create_user(
            username='stillpending', email='stillpending@example.com',
            password=PASSWORD,
        )
        EmailAddress.objects.create(
            user=member, email=member.email, verified=False, primary=True
        )

        run_migration_0014()

        self.assertTrue(EmailAddress.objects.get(user=member).verified)
        self.assertFalse(UserProfile.objects.get(user=member).is_approved)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UnlockAccountCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.out = StringIO()

    def _stuck_member(self, username='stuck', email='stuck@example.com'):
        user = User.objects.create_user(
            username=username, email=email, password=PASSWORD
        )
        EmailAddress.objects.create(
            user=user, email=email, verified=False, primary=True
        )
        return user

    def test_verifies_by_email(self):
        user = self._stuck_member()

        call_command('unlock_account', 'stuck@example.com', stdout=self.out)

        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertIn('Verified', self.out.getvalue())

    def test_verifies_by_username(self):
        user = self._stuck_member()

        call_command('unlock_account', 'stuck', stdout=self.out)

        self.assertTrue(EmailAddress.objects.get(user=user).verified)

    def test_creates_a_missing_address_row(self):
        user = User.objects.create_superuser(
            username='rootless', email='rootless@example.com', password=PASSWORD
        )
        EmailAddress.objects.filter(user=user).delete()

        call_command('unlock_account', 'rootless@example.com', stdout=self.out)

        self.assertTrue(EmailAddress.objects.get(user=user).verified)

    def test_warns_when_approval_is_still_pending(self):
        self._stuck_member()

        call_command('unlock_account', 'stuck@example.com', stdout=self.out)

        self.assertIn('still needs admin approval', self.out.getvalue())

    def test_approve_flag_clears_both_gates(self):
        user = self._stuck_member()

        call_command(
            'unlock_account', 'stuck@example.com', '--approve', stdout=self.out
        )

        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.is_approved)

        response = self.client.post(
            LOGIN_URL,
            {'login': 'stuck@example.com', 'password': PASSWORD},
            REMOTE_ADDR='198.51.100.82',
            follow=True,
        )
        self.assertTrue(logged_in(self.client))
        self.assertTrue(response.context['user'].is_authenticated)

    def test_unknown_account_errors(self):
        with self.assertRaisesMessage(CommandError, 'No account found'):
            call_command('unlock_account', 'nobody@example.com', stdout=self.out)

    def test_refuses_to_steal_a_verified_address(self):
        owner = User.objects.create_user(
            username='owner', email='taken@example.com', password=PASSWORD
        )
        EmailAddress.objects.create(
            user=owner, email='taken@example.com', verified=True, primary=True
        )
        other = User.objects.create_user(
            username='other', email='taken@example.com', password=PASSWORD
        )
        EmailAddress.objects.create(
            user=other, email='taken@example.com', verified=False, primary=True
        )

        with self.assertRaisesMessage(CommandError, 'already verified'):
            call_command('unlock_account', 'other', stdout=self.out)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AdminLoginEscapeHatchTests(TestCase):
    """
    /admin/login/ is Django's own view, so neither allauth gate applies.

    This is what a locked-out admin can use, and it is not a hole: the admin
    login form still refuses non-staff accounts.
    """

    def setUp(self):
        cache.clear()

    def test_unverified_staff_can_still_reach_the_admin(self):
        user = User.objects.create_superuser(
            username='hatch', email='hatch@example.com', password=PASSWORD
        )
        EmailAddress.objects.filter(user=user).delete()

        self.client.post(
            '/admin/login/', {'username': 'hatch', 'password': PASSWORD}
        )

        self.assertTrue(logged_in(self.client))
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_non_staff_cannot_use_the_admin_login(self):
        User.objects.create_user(
            username='outsider', email='outsider@example.com', password=PASSWORD
        )

        self.client.post(
            '/admin/login/', {'username': 'outsider', 'password': PASSWORD}
        )

        self.assertFalse(logged_in(self.client))
