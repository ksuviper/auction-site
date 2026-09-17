"""
The site is installable as an app, which means satisfying a fixed checklist.

A browser will only offer "Install" when all of this is true:

* a manifest is linked from the page, and gives a name, a start_url, a
  standalone display mode and icons;
* those icons include a 192 and a 512 pixel PNG, plus a maskable pair so
  Android can crop them to the launcher's shape;
* a service worker is registered from the site root, with a fetch handler;
* the page is served over HTTPS.

Only the last one is outside the code, so everything else is pinned here. None
of it existed before — which is exactly why Install was greyed out on Android
and absent from the desktop address bar.
"""

import io
import json
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from auctions.models import SiteSettings

SCRATCH_MEDIA = tempfile.mkdtemp(prefix='asq-pwa-media-')


def an_image(name='icon.png', size=(600, 600), colour=(30, 90, 160)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def opened(response):
    from PIL import Image

    return Image.open(io.BytesIO(response.content))


class ManifestTests(TestCase):
    def setUp(self):
        self.response = self.client.get(reverse('pwa_manifest'))
        self.doc = json.loads(self.response.content)

    def test_it_is_served_as_a_manifest(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(
            self.response['Content-Type'], 'application/manifest+json'
        )

    def test_it_carries_everything_a_browser_requires(self):
        for key in ('name', 'short_name', 'start_url', 'display', 'icons'):
            with self.subTest(key=key):
                self.assertTrue(self.doc.get(key), f'{key} missing or empty')

    def test_the_display_mode_makes_it_an_app_rather_than_a_tab(self):
        """A browser offers no install for display: browser."""
        self.assertIn(
            self.doc['display'], ('standalone', 'fullscreen', 'minimal-ui')
        )

    def test_it_starts_at_the_home_page_and_covers_the_whole_site(self):
        self.assertEqual(self.doc['start_url'], '/')
        self.assertEqual(self.doc['scope'], '/')

    def test_both_required_icon_sizes_are_offered(self):
        sizes = {
            icon['sizes'] for icon in self.doc['icons']
            if icon.get('purpose') == 'any'
        }

        self.assertIn('192x192', sizes)
        self.assertIn('512x512', sizes)

    def test_a_maskable_pair_is_offered_for_android(self):
        """Without these Android puts the icon in a white box."""
        maskable = {
            icon['sizes'] for icon in self.doc['icons']
            if icon.get('purpose') == 'maskable'
        }

        self.assertEqual(maskable, {'192x192', '512x512'})

    def test_every_icon_it_names_actually_exists(self):
        for entry in self.doc['icons']:
            with self.subTest(icon=entry['src']):
                response = self.client.get(entry['src'])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'image/png')

    def test_it_declares_colours_so_the_splash_screen_is_not_white_on_white(self):
        self.assertTrue(self.doc['theme_color'].startswith('#'))
        self.assertTrue(self.doc['background_color'].startswith('#'))

    def test_the_short_name_is_the_site_name_uncut(self):
        """
        Trimming it to the recommended dozen characters turned "Above Status
        Quo" into "Above Status". A launcher elides what will not fit, and does
        it on a word boundary, so it is better left alone.
        """
        row = SiteSettings.load()

        self.assertEqual(self.doc['short_name'], row.site_name)

    def test_the_name_follows_the_site_name_in_the_admin(self):
        row = SiteSettings.load()
        row.site_name = 'Prairie Club'
        row.site_slogan = 'Daylilies'
        row.save()

        doc = json.loads(self.client.get(reverse('pwa_manifest')).content)

        self.assertEqual(doc['name'], 'Prairie Club Daylilies')
        self.assertEqual(doc['short_name'], 'Prairie Club')

    def test_a_long_site_name_is_not_cut_mid_word(self):
        SiteSettings.objects.filter(pk=1).update(
            site_name='Above Status Quo', site_slogan=''
        )

        doc = json.loads(self.client.get(reverse('pwa_manifest')).content)

        self.assertEqual(doc['short_name'], 'Above Status Quo')

    def test_a_cleared_site_name_still_leaves_a_usable_app_name(self):
        """name is required, so it cannot be allowed to come out empty."""
        SiteSettings.objects.filter(pk=1).update(site_name='', site_slogan='')

        doc = json.loads(self.client.get(reverse('pwa_manifest')).content)

        self.assertTrue(doc['name'])
        self.assertTrue(doc['short_name'])


class IconTests(TestCase):
    def test_the_icons_are_the_exact_sizes_they_claim(self):
        for size in (192, 512):
            with self.subTest(size=size):
                image = opened(self.client.get(reverse('pwa_icon', args=[size])))
                self.assertEqual(image.size, (size, size))

    def test_maskable_icons_are_the_same_sizes(self):
        for size in (192, 512):
            with self.subTest(size=size):
                image = opened(
                    self.client.get(reverse('pwa_icon_maskable', args=[size]))
                )
                self.assertEqual(image.size, (size, size))

    def test_a_maskable_icon_keeps_its_content_away_from_the_edges(self):
        """
        Android crops a maskable icon to a circle or squircle. Content that
        runs to the edge loses its corners, so the corners must be background.
        """
        image = opened(
            self.client.get(reverse('pwa_icon_maskable', args=[512]))
        ).convert('RGB')

        for corner in ((2, 2), (509, 2), (2, 509), (509, 509)):
            with self.subTest(corner=corner):
                self.assertEqual(image.getpixel(corner), (255, 255, 255))

    def test_an_unexpected_size_is_refused(self):
        """The size comes off the URL; an open-ended one invites a huge PNG."""
        response = self.client.get('/pwa/icon-4096.png')

        self.assertEqual(response.status_code, 404)

    def test_they_are_cacheable(self):
        response = self.client.get(reverse('pwa_icon', args=[192]))

        self.assertIn('max-age', response['Cache-Control'])

    def test_the_icons_work_with_no_settings_row_at_all(self):
        """A fresh install must still be installable."""
        SiteSettings.objects.all().delete()

        response = self.client.get(reverse('pwa_icon', args=[512]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(opened(response).size, (512, 512))


@override_settings(MEDIA_ROOT=SCRATCH_MEDIA)
class UploadedIconTests(TestCase):
    def test_an_uploaded_icon_is_what_gets_installed(self):
        """
        The point of generating these rather than shipping fixed files: the
        installed app matches whatever branding the admin uploaded.
        """
        row = SiteSettings.load()
        row.app_icon_image = an_image(colour=(200, 40, 40))
        row.save()

        image = opened(
            self.client.get(reverse('pwa_icon', args=[192]))
        ).convert('RGB')

        self.assertEqual(image.getpixel((96, 96)), (200, 40, 40))

    def test_uploading_a_new_icon_replaces_the_cached_one(self):
        row = SiteSettings.load()
        row.app_icon_image = an_image('first.png', colour=(200, 40, 40))
        row.save()
        self.client.get(reverse('pwa_icon', args=[192]))

        row.app_icon_image = an_image('second.png', colour=(20, 160, 60))
        row.save()

        image = opened(
            self.client.get(reverse('pwa_icon', args=[192]))
        ).convert('RGB')
        self.assertEqual(image.getpixel((96, 96)), (20, 160, 60))

    def test_a_wide_image_is_letterboxed_rather_than_squashed(self):
        row = SiteSettings.load()
        row.app_icon_image = an_image('wide.png', size=(800, 200))
        row.save()

        image = opened(
            self.client.get(reverse('pwa_icon', args=[192]))
        ).convert('RGB')

        # Square canvas, with background above and below rather than a
        # stretched image.
        self.assertEqual(image.size, (192, 192))
        self.assertEqual(image.getpixel((96, 4)), (255, 255, 255))


class ServiceWorkerTests(TestCase):
    def test_it_is_served_from_the_site_root(self):
        """
        A worker only controls URLs at or below its own path. Served from
        /static/ it would control /static/ and nothing else, and the site would
        not be installable.
        """
        self.assertEqual(reverse('pwa_service_worker'), '/sw.js')

    def test_it_is_served_as_javascript(self):
        response = self.client.get('/sw.js')

        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response['Content-Type'])

    def test_it_handles_fetch_which_is_what_installability_requires(self):
        body = self.client.get('/sw.js').content.decode()

        self.assertIn("addEventListener('fetch'", body)

    def test_it_falls_back_to_the_offline_page(self):
        body = self.client.get('/sw.js').content.decode()

        self.assertIn(reverse('pwa_offline'), body)

    def test_it_leaves_anything_that_is_not_a_page_load_alone(self):
        """
        The guard that keeps a cache from ever corrupting a bid or a purchase.
        """
        body = self.client.get('/sw.js').content.decode()

        self.assertIn("request.method !== 'GET'", body)
        self.assertIn("request.mode !== 'navigate'", body)

    def test_it_carries_a_version_so_an_update_retires_the_old_cache(self):
        body = self.client.get('/sw.js').content.decode()

        self.assertIn('const VERSION', body)
        self.assertNotIn('{{ version }}', body)


class OfflinePageTests(TestCase):
    def test_it_renders(self):
        response = self.client.get(reverse('pwa_offline'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'offline')

    def test_it_does_not_depend_on_the_site_chrome(self):
        """
        It is shown when nothing is reachable. base.html pulls Bootstrap and
        Font Awesome off a CDN and queries the database for the sidebar, none
        of which is available at that moment.
        """
        body = self.client.get(reverse('pwa_offline')).content.decode()

        self.assertNotIn('cdn.jsdelivr.net', body)
        self.assertNotIn('cdnjs.cloudflare.com', body)
        self.assertNotIn('Browse by Category', body)

    def test_it_still_names_the_site(self):
        row = SiteSettings.load()
        row.site_name = 'Prairie Club'
        row.save()

        self.assertContains(self.client.get(reverse('pwa_offline')), 'Prairie Club')


class PageWiringTests(TestCase):
    """A manifest nothing links to is a manifest no browser reads."""

    def setUp(self):
        self.body = self.client.get(reverse('home')).content.decode()

    def test_the_manifest_is_linked(self):
        self.assertIn(
            f'<link rel="manifest" href="{reverse("pwa_manifest")}">', self.body
        )

    def test_the_worker_is_registered(self):
        self.assertIn('js/pwa.js', self.body)

    def test_a_theme_colour_is_declared(self):
        self.assertIn('name="theme-color"', self.body)

    def test_ios_gets_its_own_icon_because_it_ignores_the_manifest(self):
        self.assertIn(
            f'<link rel="apple-touch-icon" href="{reverse("pwa_icon", args=[192])}">',
            self.body,
        )

    def test_every_page_carries_it_not_just_the_home_page(self):
        for url in (reverse('faq'), reverse('about_us')):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertIn('rel="manifest"', body)
