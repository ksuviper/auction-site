"""Tests for the admin-managed site images, and two navigation moves.

SiteSettings is a singleton read on every page render, so the ways it can go
wrong are: a second row appearing, the row being missing on a fresh install,
and a failure there taking every page down with it. All three are covered.

The banner and the icon are separate fields on purpose — a wide banner cropped
square makes a bad icon — so nothing here derives one from the other.
"""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from auctions.models import SiteSettings, Subscription, UserProfile

User = get_user_model()

# The admin upload tests write real files. Pointing MEDIA_ROOT at a scratch
# directory keeps them out of the working tree — the first run of this module
# left two PNGs under media/site/ that nearly went into a commit.
SCRATCH_MEDIA = tempfile.mkdtemp(prefix='asq-test-media-')


def an_image(name='pic.png', size=(40, 40), colour=(120, 180, 90)):
    """A real PNG, so ImageField's own validation is exercised."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class SingletonTests(TestCase):
    def test_load_creates_the_row_on_first_use(self):
        SiteSettings.objects.all().delete()

        settings_row = SiteSettings.load()

        self.assertEqual(settings_row.pk, 1)
        self.assertEqual(SiteSettings.objects.count(), 1)

    def test_load_returns_the_same_row_every_time(self):
        first = SiteSettings.load()
        first.header_banner_image = 'site/banner.png'
        first.save()

        self.assertEqual(SiteSettings.load().pk, first.pk)
        self.assertEqual(
            SiteSettings.load().header_banner_image.name, 'site/banner.png'
        )

    def test_saving_a_second_row_overwrites_the_first(self):
        """
        Pinned to pk=1, so no route creates a second — not a stray POST, not a
        fixture, not a shell session. load() would otherwise pick one
        arbitrarily and the site would flicker between them.
        """
        SiteSettings.load()
        SiteSettings(header_banner_image='site/other.png').save()

        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(
            SiteSettings.load().header_banner_image.name, 'site/other.png'
        )

    def test_deleting_clears_the_images_instead_of_dropping_the_row(self):
        settings_row = SiteSettings.load()
        settings_row.header_banner_image = 'site/banner.png'
        settings_row.app_icon_image = 'site/icon.png'
        settings_row.save()

        settings_row.delete()

        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertFalse(SiteSettings.load().header_banner_image)
        self.assertFalse(SiteSettings.load().app_icon_image)


@override_settings(MEDIA_URL='/media/')
class HeaderAndIconRenderTests(TestCase):
    def setUp(self):
        SiteSettings.objects.all().delete()

    def test_a_fresh_install_falls_back_to_the_built_in_images(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, 'img/logo.png')
        self.assertContains(response, 'favicon.ico')

    def test_an_uploaded_banner_replaces_the_logo(self):
        settings_row = SiteSettings.load()
        settings_row.header_banner_image = 'site/banner.png'
        settings_row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, '/media/site/banner.png')
        self.assertNotContains(response, 'img/logo.png')

    def test_an_uploaded_icon_becomes_the_tab_and_home_screen_icon(self):
        settings_row = SiteSettings.load()
        settings_row.app_icon_image = 'site/icon.png'
        settings_row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'rel="icon" href="/media/site/icon.png"')
        self.assertContains(
            response, 'rel="apple-touch-icon" href="/media/site/icon.png"'
        )
        self.assertNotContains(response, 'favicon.ico')

    def test_the_two_images_are_independent(self):
        """Neither is derived from the other — that is the whole point."""
        settings_row = SiteSettings.load()
        settings_row.header_banner_image = 'site/wide.png'
        settings_row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, '/media/site/wide.png')
        # No banner-as-icon: the icon falls back on its own.
        self.assertContains(response, 'favicon.ico')

    def test_the_banner_is_width_capped_without_relying_on_the_stylesheet(self):
        """
        A wide banner overflowed a phone screen when Bootstrap failed to load.

        The built-in logo is tall enough that max-height alone keeps it narrow,
        so this only shows up once an admin uploads the wide image the field is
        for. Caught by measuring a real browser with the CDN blocked.
        """
        settings_row = SiteSettings.load()
        settings_row.header_banner_image = 'site/banner.png'
        settings_row.save()

        html = self.client.get(reverse('home')).content.decode()
        banner_tag = html[html.index('site/banner.png') - 400:]
        style = banner_tag[banner_tag.index('style="'):]
        style = style[:style.index('"', 7)]

        self.assertIn('max-width: 100%', style)

    def test_the_images_appear_on_every_page_not_just_the_homepage(self):
        settings_row = SiteSettings.load()
        settings_row.header_banner_image = 'site/banner.png'
        settings_row.save()

        for url in (reverse('faq'), reverse('about_us')):
            with self.subTest(url=url):
                self.assertContains(
                    self.client.get(url), '/media/site/banner.png'
                )

    def test_a_broken_settings_lookup_does_not_take_the_site_down(self):
        """
        The context processor runs on every render, error pages included.

        A database problem there must give a page with the built-in logo, not a
        500 on top of whatever went wrong.
        """
        from unittest.mock import patch

        with patch.object(
            SiteSettings, 'load', side_effect=RuntimeError('database gone')
        ):
            with self.assertLogs('auction_site.context_processors', 'ERROR'):
                response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'img/logo.png')


@override_settings(MEDIA_ROOT=SCRATCH_MEDIA)
class SiteSettingsAdminTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(
            'imageboss', 'imageboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

    def test_the_list_view_goes_straight_to_the_single_row(self):
        response = self.client.get(
            reverse('admin:auctions_sitesettings_changelist')
        )

        self.assertRedirects(
            response,
            reverse('admin:auctions_sitesettings_change', args=[1]),
        )

    def test_the_edit_page_offers_both_uploads_separately(self):
        SiteSettings.load()

        response = self.client.get(
            reverse('admin:auctions_sitesettings_change', args=[1])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="header_banner_image"')
        self.assertContains(response, 'name="app_icon_image"')
        self.assertContains(response, 'Header banner')
        self.assertContains(response, 'App icon')

    def test_the_edit_page_says_the_icon_should_be_square(self):
        SiteSettings.load()

        response = self.client.get(
            reverse('admin:auctions_sitesettings_change', args=[1])
        )

        self.assertContains(response, 'square')

    def test_adding_a_second_row_is_refused(self):
        response = self.client.get(reverse('admin:auctions_sitesettings_add'))

        self.assertEqual(response.status_code, 403)

    def test_deleting_is_refused(self):
        SiteSettings.load()

        response = self.client.get(
            reverse('admin:auctions_sitesettings_delete', args=[1])
        )

        self.assertEqual(response.status_code, 403)

    def test_uploading_a_banner_through_the_admin_works(self):
        SiteSettings.load()

        self.client.post(
            reverse('admin:auctions_sitesettings_change', args=[1]),
            {'header_banner_image': an_image('banner.png', size=(300, 100))},
        )

        self.assertTrue(SiteSettings.load().header_banner_image)

    def test_uploading_an_icon_through_the_admin_works(self):
        SiteSettings.load()

        self.client.post(
            reverse('admin:auctions_sitesettings_change', args=[1]),
            {'app_icon_image': an_image('icon.png', size=(512, 512))},
        )

        self.assertTrue(SiteSettings.load().app_icon_image)


class MembershipRelocationTests(TestCase):
    """Item 19: out of the main navigation, onto the profile page."""

    def setUp(self):
        self.user = User.objects.create_user('memb', 'memb@example.com', 'pw')
        self.client.force_login(self.user)

    def test_the_main_nav_no_longer_carries_a_membership_link(self):
        response = self.client.get(reverse('home'))
        # The nav is everything before <main>; the footer and page body are not
        # what moved.
        nav = response.content.decode().split('<main')[0]

        self.assertNotIn('Membership', nav)

    def test_the_profile_page_links_to_membership(self):
        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'Manage Membership')
        self.assertContains(response, reverse('subscribe'))

    def test_the_profile_page_states_the_current_status(self):
        Subscription.objects.create(
            user=self.user, plan='monthly', status='active'
        )

        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'Active')
        self.assertContains(response, 'You can bid and buy')

    def test_a_member_with_no_subscription_is_told_one_is_needed(self):
        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'No membership yet')

    def test_an_exempt_account_is_told_it_does_not_need_one(self):
        UserProfile.objects.filter(user=self.user).update(
            subscription_required=False
        )

        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'Not required')

    def test_a_lapsed_payment_still_warns_in_the_navigation(self):
        """
        The one thing that stays in the nav.

        A payment problem stops you bidding, so it has to be visible wherever
        you are rather than only if you go looking at your profile.
        """
        Subscription.objects.create(
            user=self.user, plan='monthly', status='lapsed'
        )

        response = self.client.get(reverse('home'))
        nav = response.content.decode().split('<main')[0]

        self.assertIn('Membership', nav)
        self.assertIn('payment issue', nav)

    def test_an_active_member_gets_no_nav_warning(self):
        Subscription.objects.create(
            user=self.user, plan='monthly', status='active'
        )

        response = self.client.get(reverse('home'))
        nav = response.content.decode().split('<main')[0]

        self.assertNotIn('Membership', nav)


class WelcomeBannerPositionTests(TestCase):
    """Item 20: the banner sits below the heading, not above it."""

    def test_the_heading_comes_before_the_welcome_text(self):
        html = self.client.get(reverse('home')).content.decode()

        heading = html.index('Welcome to ASQ Daylily Auctions')
        banner = html.index('Discover and bid on premium daylilies')

        self.assertLess(heading, banner)

    def test_the_heading_is_the_pages_h1_and_sits_outside_the_banner(self):
        """It used to be inside the decorative box, which is what moved."""
        html = self.client.get(reverse('home')).content.decode()

        self.assertIn(
            '<h1 class="h2 fw-bold mb-3">Welcome to ASQ Daylily Auctions</h1>',
            html,
        )

    def test_the_welcome_text_is_still_there(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, 'New sellers are featured each week')
