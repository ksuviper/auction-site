"""
One place to edit a person, and the profile list kept only as a worklist.

A profile is one-to-one with a user, and it used to be editable in two places:
inline on the user page, and on its own change page, with 19 of its 21 fields
identical on both. Two forms over one record is a way to lose an edit.

The profile changelist itself stays. It is the only place bulk approval works:
Django's list_editable only accepts fields of the model being listed, and
is_approved lives on UserProfile rather than on User, so the user changelist
cannot offer those checkboxes at all. These tests pin both halves — the
duplicate editor is gone, and every capability that justified keeping the list
still functions.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from auctions.admin import (
    PROFILE_FIELDSETS,
    CustomUserAdmin,
    UserProfileAdmin,
    UserProfileInline,
)
from auctions.models import UserProfile

User = get_user_model()


def fieldset_names(fieldsets):
    return [name for _label, opts in fieldsets for name in opts['fields']]


class OneEditingSurfaceTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'profileboss', 'profileboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.member = User.objects.create_user(
            'amember', 'amember@example.com', 'pw',
        )
        self.client.force_login(self.staff)

    def test_opening_a_profile_redirects_to_its_account(self):
        response = self.client.get(
            reverse(
                'admin:auctions_userprofile_change',
                args=[self.member.profile.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse('admin:auth_user_change', args=[self.member.pk]),
        )

    def test_an_unknown_profile_still_gets_the_normal_not_found_page(self):
        """The redirect must not turn a bad id into a crash."""
        response = self.client.get(
            reverse('admin:auctions_userprofile_change', args=[999999])
        )

        self.assertIn(response.status_code, (302, 404))
        if response.status_code == 302:
            self.assertNotIn('/auth/user/', response['Location'])

    def test_the_inline_carries_every_editable_profile_field(self):
        """
        Nothing may be stranded on a page that is now unreachable. The
        login-code toggle was, which is what prompted this.
        """
        editable = {
            field.name
            for field in UserProfile._meta.get_fields()
            if getattr(field, 'editable', False) and not field.auto_created
        }
        # 'user' is the link to the account whose page this renders on, so it is
        # the one field that would be meaningless here.
        expected = editable - {'user'}

        self.assertEqual(set(fieldset_names(UserProfileInline.fieldsets)), expected)

    def test_the_login_code_toggle_reaches_the_user_page(self):
        response = self.client.get(
            reverse('admin:auth_user_change', args=[self.member.pk])
        )

        self.assertContains(response, 'email_login_code_enabled')

    def test_the_seller_guidance_reaches_the_user_page(self):
        """The captions explaining the seller fields moved with them."""
        response = self.client.get(
            reverse('admin:auth_user_change', args=[self.member.pk])
        )

        self.assertContains(response, 'Only used when')
        self.assertContains(response, 'read-only sales dashboard')

    def test_editing_through_the_user_page_saves_the_profile(self):
        """The surviving surface has to actually work end to end."""
        url = reverse('admin:auth_user_change', args=[self.member.pk])
        form = self.client.get(url).context['adminform'].form
        data = {
            'username': self.member.username,
            'email': self.member.email,
            'first_name': '',
            'last_name': '',
            'date_joined_0': '2026-01-01',
            'date_joined_1': '00:00:00',
            'profile-TOTAL_FORMS': '1',
            'profile-INITIAL_FORMS': '1',
            'profile-MIN_NUM_FORMS': '0',
            'profile-MAX_NUM_FORMS': '1',
            'profile-0-id': str(self.member.profile.pk),
            'profile-0-user': str(self.member.pk),
            'profile-0-phone_number': '555-0100',
            'profile-0-country': 'US',
            'profile-0-address': '',
            'profile-0-notes': '',
            'profile-0-is_approved': 'on',
            'profile-0-is_seller': 'on',
            'profile-0-seller_bio': '',
            'profile-0-seller_active_week': '',
            'profile-0-seller_shipping_fee': '',
            'profile-0-seller_notify_on_comments': 'on',
            'profile-0-seller_payment_methods': '',
            'profile-0-venmo_info': '@handle',
            'profile-0-paypal_info': '',
            'profile-0-cashapp_info': '',
            'profile-0-apple_pay_info': '',
            'profile-0-google_pay_info': '',
            'profile-0-zelle_info': '',
            '_save': 'Save',
        }
        self.assertIn('username', form.fields)

        self.client.post(url, data)

        self.member.profile.refresh_from_db()
        self.assertEqual(self.member.profile.phone_number, '555-0100')
        self.assertTrue(self.member.profile.is_seller)
        self.assertEqual(self.member.profile.venmo_info, '@handle')

    def test_the_inline_is_the_only_definition_of_the_layout(self):
        """
        There is nothing left to drift from. The profile admin renders no form
        at all now — its change page redirects and its add page is gone — so
        the shared definition has exactly one consumer.
        """
        self.assertEqual(UserProfileInline.fieldsets, PROFILE_FIELDSETS)

        profile_admin = UserProfileAdmin(UserProfile, AdminSite())
        request = RequestFactory().get('/admin/')
        request.user = self.staff

        self.assertFalse(profile_admin.has_add_permission(request))


class TheListStillDoesItsJobTests(TestCase):
    """Everything that justified keeping the profile changelist."""

    def setUp(self):
        self.staff = User.objects.create_user(
            'listboss', 'listboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)
        self.waiting = User.objects.create_user(
            'waiting', 'waiting@example.com', 'pw',
        )
        UserProfile.objects.filter(user=self.waiting).update(is_approved=False)

    def test_the_changelist_still_opens(self):
        response = self.client.get(
            reverse('admin:auctions_userprofile_changelist')
        )

        self.assertEqual(response.status_code, 200)

    def test_the_approval_queue_link_still_works(self):
        response = self.client.get(
            reverse('admin:auctions_userprofile_changelist') + '?approval=pending'
        )

        self.assertEqual(response.status_code, 200)
        rows = response.context['cl'].result_list
        self.assertIn(self.waiting.profile.pk, [row.pk for row in rows])

    def test_the_seller_roster_link_still_works(self):
        UserProfile.objects.filter(user=self.waiting).update(is_seller=True)

        response = self.client.get(
            reverse('admin:auctions_userprofile_changelist')
            + '?is_seller__exact=1'
        )

        self.assertEqual(response.status_code, 200)
        rows = response.context['cl'].result_list
        self.assertEqual([row.pk for row in rows], [self.waiting.profile.pk])

    def test_tick_in_place_editing_survives(self):
        """
        The reason this admin is still registered. Revoking change permission
        to stop the duplicate form would have silently killed this, so the
        permission is asserted rather than assumed.
        """
        profile_admin = UserProfileAdmin(UserProfile, AdminSite())
        request = RequestFactory().get(
            reverse('admin:auctions_userprofile_changelist')
        )
        request.user = self.staff

        self.assertEqual(
            profile_admin.list_editable,
            ('is_approved', 'is_seller', 'subscription_required'),
        )
        self.assertTrue(profile_admin.has_change_permission(request))

    def test_the_checkboxes_actually_render_in_the_list(self):
        """list_editable is only real if the widgets reach the page."""
        response = self.client.get(
            reverse('admin:auctions_userprofile_changelist')
        )

        self.assertContains(response, 'form-0-is_approved')

    def test_bulk_approval_still_runs_from_the_list(self):
        response = self.client.post(
            reverse('admin:auctions_userprofile_changelist'),
            {
                'action': 'approve_selected_users',
                '_selected_action': [str(self.waiting.profile.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.waiting.profile.refresh_from_db()
        self.assertTrue(self.waiting.profile.is_approved)

    def test_the_user_changelist_cannot_replace_it(self):
        """
        Why the list was not simply folded into Users: list_editable only takes
        fields of the model being listed, and is_approved is not on User. The
        columns there are computed, so they are read-only by construction.
        """
        user_admin = CustomUserAdmin(User, AdminSite())

        self.assertEqual(user_admin.list_editable, ())
        self.assertFalse(hasattr(User, 'is_approved'))
        self.assertIn('approval_status', user_admin.list_display)


class SidebarTests(TestCase):
    """Item 1: the unfiltered profile entry is gone, the filtered ones remain."""

    def setUp(self):
        staff = User.objects.create_user(
            'navboss', 'navboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

    def entries(self):
        from django.conf import settings

        return [
            item
            for group in settings.UNFOLD['SIDEBAR']['navigation']
            for item in group['items']
        ]

    def test_no_plain_user_profiles_entry(self):
        titles = [str(item['title']) for item in self.entries()]

        self.assertNotIn('User Profiles', titles)

    def test_the_filtered_views_are_still_there(self):
        titles = [str(item['title']) for item in self.entries()]

        self.assertIn('Pending Approval', titles)
        self.assertIn('Sellers', titles)
        self.assertIn('Users', titles)

    def test_every_sidebar_link_resolves(self):
        """A removed entry must not leave a neighbour pointing at nothing."""
        for item in self.entries():
            with self.subTest(title=str(item['title'])):
                response = self.client.get(str(item['link']))
                self.assertIn(response.status_code, (200, 302))
