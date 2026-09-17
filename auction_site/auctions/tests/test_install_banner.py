"""
The install bar: markup on every page, and the rules the script follows.

The bar exists because the browser's own install affordance is easy to miss —
an icon in the desktop address bar, a menu entry on Android — and because iOS
Safari has none at all. On iOS the manual instructions are the only way a
visitor will ever discover the app, which is why the two platforms get
different content rather than the same bar with a disabled button.

Python can check the markup is present and correctly inert, and can read the
script for the rules it must follow. What it cannot do is fire a
'beforeinstallprompt'; that is checked in a real browser separately.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from auctions.models import SiteSettings

SCRIPT = (
    Path(settings.BASE_DIR) / 'auction_site' / 'static' / 'js' / 'install-banner.js'
)
STYLESHEET = Path(settings.BASE_DIR) / 'auction_site' / 'static' / 'style.css'


class MarkupTests(TestCase):
    def setUp(self):
        self.body = self.client.get(reverse('home')).content.decode()

    def test_the_bar_is_on_the_page(self):
        self.assertIn('id="pwa-install-banner"', self.body)

    def test_it_starts_hidden(self):
        """
        Nothing decides whether to show it until the script runs. Rendering it
        visible and hiding it afterwards would flash on every page load.
        """
        banner = self.body[self.body.index('id="pwa-install-banner"'):][:400]

        self.assertIn('hidden', banner)

    def test_both_wordings_are_rendered_for_the_script_to_choose_between(self):
        self.assertIn('data-pwa-message="prompt"', self.body)
        self.assertIn('data-pwa-message="ios"', self.body)

    def test_the_ios_wording_gives_the_actual_steps(self):
        """Safari offers no button, so the steps are all there is."""
        self.assertIn('tap the Share icon', self.body)
        self.assertIn('Add to Home Screen', self.body)

    def test_the_install_button_starts_hidden_so_it_is_never_dead(self):
        """
        It only works once 'beforeinstallprompt' has fired. Showing it before
        that would be a button that does nothing.
        """
        button = self.body[self.body.index('data-pwa-action="install"'):][:200]

        self.assertIn('hidden', button)

    def test_there_is_a_dismiss_control(self):
        self.assertIn('data-pwa-action="dismiss"', self.body)
        self.assertIn('aria-label="Dismiss this message"', self.body)

    def test_the_bar_names_the_site_from_the_branding_row(self):
        row = SiteSettings.load()
        row.site_name = 'Prairie Daylily Club'
        row.save()

        body = self.client.get(reverse('home')).content.decode()

        self.assertIn('Install Prairie Daylily Club for quick access', body)

    def test_the_icon_is_the_generated_one_not_a_hardcoded_file(self):
        self.assertIn(f'data-pwa-icon="{reverse("pwa_icon", args=[192])}"', self.body)

    def test_the_icon_is_not_fetched_until_the_bar_is_shown(self):
        """
        src is set by the script. Putting the URL straight in src would fetch
        it on every page view for the few that ever show the bar.
        """
        banner = self.body[self.body.index('id="pwa-install-banner"'):][:600]

        self.assertNotIn('<img src=', banner)

    def test_it_is_on_every_page_not_just_the_home_page(self):
        for url in (reverse('faq'), reverse('about_us')):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertIn('id="pwa-install-banner"', body)

    def test_the_script_is_loaded(self):
        self.assertIn('js/install-banner.js', self.body)


class ScriptRulesTests(TestCase):
    """The behaviours the prompt is specific about, read from the source."""

    def setUp(self):
        self.source = SCRIPT.read_text()

    def test_the_script_exists(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertIn('pwa-install-banner', self.source)

    def test_it_says_nothing_when_the_app_is_already_installed(self):
        self.assertIn("'(display-mode: standalone)'", self.source)
        self.assertIn('navigator.standalone', self.source)

    def test_it_remembers_a_dismissal(self):
        """A bar that returns on every page is worse than no bar."""
        self.assertIn('pwaBannerDismissedAt', self.source)
        self.assertIn('DISMISSED_DAYS = 14', self.source)

    def test_it_survives_storage_being_unavailable(self):
        """localStorage throws in a private window; a bar is not worth a crash."""
        self.assertIn('try {', self.source)
        self.assertIn('catch', self.source)

    def test_it_captures_the_install_event_rather_than_letting_it_pass(self):
        self.assertIn("'beforeinstallprompt'", self.source)
        self.assertIn('event.preventDefault()', self.source)

    def test_it_replays_the_browsers_own_dialog(self):
        self.assertIn('deferredPrompt.prompt()', self.source)
        self.assertIn('userChoice', self.source)

    def test_it_detects_an_ipad_pretending_to_be_a_mac(self):
        """iPadOS 13+ reports MacIntel; touch points are what still separate it."""
        self.assertIn('maxTouchPoints', self.source)

    def test_it_hides_itself_once_the_app_is_installed(self):
        self.assertIn("'appinstalled'", self.source)

    def test_ios_never_gets_a_button_that_cannot_work(self):
        """
        Apple provides no way to trigger an install. The iOS path shows the
        instructions and leaves the button hidden.
        """
        ios_branch = self.source[self.source.index('if (isIOS())'):]
        ios_branch = ios_branch[:ios_branch.index('}')]

        self.assertIn("show('ios')", ios_branch)
        self.assertNotIn('installButton.hidden = false', ios_branch)


class StylingTests(TestCase):
    def setUp(self):
        self.css = STYLESHEET.read_text()

    def test_the_hidden_attribute_actually_hides_it(self):
        """
        A display: flex rule would otherwise override the attribute's default,
        leaving the bar on screen permanently.
        """
        self.assertIn('.pwa-install-banner[hidden]', self.css)

    def test_it_is_kept_off_wide_screens(self):
        self.assertIn('min-width: 992px', self.css)
        block = self.css[self.css.index('.pwa-install-banner {'):]

        self.assertIn('display: none !important', block)


class PwaStillIntactTests(TestCase):
    """The bar is additive. It must not disturb what makes the site installable."""

    def test_the_manifest_is_unchanged_and_still_linked(self):
        body = self.client.get(reverse('home')).content.decode()

        self.assertIn(f'<link rel="manifest" href="{reverse("pwa_manifest")}">', body)
        self.assertEqual(
            self.client.get(reverse('pwa_manifest')).status_code, 200
        )

    def test_the_service_worker_is_untouched(self):
        """
        This task adds no caching and does not change sw.js, so
        SERVICE_WORKER_VERSION should not have needed bumping.
        """
        from auction_site.pwa import SERVICE_WORKER_VERSION

        self.assertEqual(SERVICE_WORKER_VERSION, 'v1')

    def test_the_worker_still_caches_only_the_offline_page(self):
        worker = self.client.get('/sw.js').content.decode()

        self.assertIn(reverse('pwa_offline'), worker)
        self.assertNotIn('install-banner', worker)

    def test_the_offline_page_still_works(self):
        self.assertEqual(self.client.get(reverse('pwa_offline')).status_code, 200)
