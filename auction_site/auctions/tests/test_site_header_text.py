"""
The header's wording is admin-managed, and each line disappears when blank.

Two things have to hold together. The site must look unchanged until somebody
edits it, because the field defaults are the wording base.html used to hardcode.
And a blank field has to mean "an admin cleared this" rather than "something
went wrong", which is why a failed settings lookup falls back to the defaults
instead of an empty header.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from auctions.models import SiteSettings

User = get_user_model()

SHIPPED_NAME = 'Above Status Quo'
SHIPPED_SLOGAN = 'Daylily Auction Group'


def header_of(response):
    """
    Just the <header> block.

    The assertions have to be scoped because the same words still appear in the
    page title, the meta description and the footer, which remain hardcoded —
    a known limit of this change, not something these tests should paper over
    by matching the whole page.
    """
    html = response.content.decode()
    start = html.index('<header')
    return html[start:html.index('</header>', start)]


class DefaultsTests(TestCase):
    def test_a_fresh_row_carries_the_wording_the_site_shipped_with(self):
        SiteSettings.objects.all().delete()

        row = SiteSettings.load()

        self.assertEqual(row.site_name, SHIPPED_NAME)
        self.assertEqual(row.site_slogan, SHIPPED_SLOGAN)
        self.assertEqual(row.site_extra_line, '')

    def test_the_header_is_unchanged_before_anyone_edits_it(self):
        header = header_of(self.client.get(reverse('home')))

        self.assertIn(SHIPPED_NAME, header)
        self.assertIn(SHIPPED_SLOGAN, header)

    def test_the_third_line_shows_nothing_until_it_is_filled_in(self):
        header = header_of(self.client.get(reverse('home')))

        self.assertNotIn('text-muted small mb-0', header)


class EditingTests(TestCase):
    def setUp(self):
        self.row = SiteSettings.load()

    def test_changing_the_name_changes_every_page(self):
        self.row.site_name = 'Prairie Daylily Club'
        self.row.save()

        for url in (reverse('home'), reverse('faq'), reverse('about_us')):
            with self.subTest(url=url):
                header = header_of(self.client.get(url))
                self.assertIn('Prairie Daylily Club', header)
                self.assertNotIn(SHIPPED_NAME, header)

    def test_changing_the_slogan_changes_every_page(self):
        self.row.site_slogan = 'Growing since 1998'
        self.row.save()

        header = header_of(self.client.get(reverse('home')))

        self.assertIn('Growing since 1998', header)
        self.assertNotIn(SHIPPED_SLOGAN, header)

    def test_the_third_line_appears_once_filled_in(self):
        self.row.site_extra_line = 'Members meet the first Tuesday'
        self.row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Members meet the first Tuesday')

    def test_clearing_the_name_removes_it_rather_than_restoring_the_default(self):
        """
        The point of allowing blank: a banner image often has the name in it
        already, and showing it again underneath looks wrong.
        """
        self.row.site_name = ''
        self.row.save()

        header = header_of(self.client.get(reverse('home')))

        self.assertNotIn(SHIPPED_NAME, header)
        # The slogan is untouched by clearing the name.
        self.assertIn(SHIPPED_SLOGAN, header)

    def test_clearing_every_line_leaves_a_header_with_just_the_image(self):
        SiteSettings.objects.filter(pk=1).update(
            site_name='', site_slogan='', site_extra_line=''
        )

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        header = header_of(response)
        self.assertNotIn(SHIPPED_NAME, header)
        self.assertNotIn(SHIPPED_SLOGAN, header)
        # The image is still there; only the words went.
        self.assertIn('<img', header)

    def test_the_wording_is_escaped(self):
        self.row.site_name = '<script>alert(1)</script>'
        self.row.save()

        response = self.client.get(reverse('home'))

        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_the_image_alt_text_follows_the_site_name(self):
        """Otherwise a rename leaves screen readers announcing the old one."""
        self.row.site_name = 'Prairie Daylily Club'
        self.row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'alt="Prairie Daylily Club"')

    def test_the_alt_text_falls_back_when_the_name_is_cleared(self):
        self.row.site_name = ''
        self.row.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'alt="Site logo"')


class FallbackTests(TestCase):
    def test_a_broken_settings_lookup_still_shows_the_name_and_slogan(self):
        """
        Previously the fallback was None, which was fine while the wording was
        hardcoded. With the wording in the row, None would have emptied the
        header on any database hiccup — so the fallback is an unsaved instance,
        which carries the field defaults and touches no database.
        """
        with patch.object(
            SiteSettings, 'load', side_effect=RuntimeError('database gone')
        ):
            with self.assertLogs('auction_site.context_processors', 'ERROR'):
                response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        header = header_of(response)
        self.assertIn(SHIPPED_NAME, header)
        self.assertIn(SHIPPED_SLOGAN, header)
        # And still no uploaded images, so the built-in logo is used.
        self.assertIn('img/logo.png', header)


class ResetTests(TestCase):
    def test_removing_the_settings_restores_the_shipped_wording(self):
        row = SiteSettings.load()
        row.site_name = 'Something Else'
        row.site_slogan = 'Another slogan'
        row.site_extra_line = 'Third line'
        row.save()

        row.delete()

        row = SiteSettings.load()
        self.assertEqual(row.site_name, SHIPPED_NAME)
        self.assertEqual(row.site_slogan, SHIPPED_SLOGAN)
        self.assertEqual(row.site_extra_line, '')
        self.assertEqual(SiteSettings.objects.count(), 1)


class AdminTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(
            'brandboss', 'brandboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)
        SiteSettings.load()

    def url(self):
        return reverse('admin:auctions_sitesettings_change', args=[1])

    def test_the_three_lines_are_on_the_form(self):
        response = self.client.get(self.url())

        for field in ('site_name', 'site_slogan', 'site_extra_line'):
            with self.subTest(field=field):
                self.assertContains(response, f'name="{field}"')

    def test_the_form_explains_what_the_lines_are(self):
        response = self.client.get(self.url())

        self.assertContains(response, 'Header text')
        self.assertContains(response, 'left out when empty')

    def test_saving_the_form_updates_the_header(self):
        self.client.post(
            self.url(),
            {
                'site_name': 'Prairie Daylily Club',
                'site_slogan': 'Growing since 1998',
                'site_extra_line': 'Third line here',
                'header_banner_image': '',
                'app_icon_image': '',
            },
        )

        header = header_of(self.client.get(reverse('home')))
        self.assertIn('Prairie Daylily Club', header)
        self.assertIn('Growing since 1998', header)
        self.assertIn('Third line here', header)

    def test_the_sidebar_entry_is_named_for_what_it_holds(self):
        from django.conf import settings

        titles = [
            str(item['title'])
            for group in settings.UNFOLD['SIDEBAR']['navigation']
            for item in group['items']
        ]

        self.assertIn('Branding', titles)
        self.assertNotIn('Images', titles)
