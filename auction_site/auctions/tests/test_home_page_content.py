"""
Every word on the homepage is admin-editable, and none of it can go missing.

The welcome heading and text, and the heading above the step cards, are SitePage
rows. The cards themselves are HomePageStep rows, so an admin can reword,
reorder, unpublish or add one. About Us was already read from the page behind
/about/.

The risk this introduces is a blank homepage: content that used to be in the
template can now be deleted, or absent on a database restored without it. So
every lookup falls back to the wording the page shipped with, and those
fallbacks are tested as carefully as the editing.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from auctions.models import HomePageStep, SitePage

User = get_user_model()

SHIPPED_HEADING = 'Welcome to ASQ Daylily Auctions'
SHIPPED_WELCOME = 'Discover and bid on premium daylilies'


def main_of(response):
    """The page body, excluding the site header and footer chrome."""
    html = response.content.decode()
    start = html.index('<main')
    return html[start:html.index('</main>', start)]


class SeededContentTests(TestCase):
    """The migration's rows, which every install starts from."""

    def test_the_welcome_page_exists(self):
        page = SitePage.objects.get(slug='home')

        self.assertEqual(page.title, SHIPPED_HEADING)
        self.assertIn('premium daylilies', page.body)

    def test_the_steps_heading_page_exists(self):
        page = SitePage.objects.get(slug='home-how-it-works')

        self.assertEqual(page.title, 'How It Works')

    def test_the_three_steps_are_seeded_in_order(self):
        titles = list(
            HomePageStep.objects.filter(is_published=True)
            .values_list('title', flat=True)
        )

        self.assertEqual(titles, ['Browse', 'Bid', 'Win & Enjoy'])

    def test_the_homepage_is_unchanged_before_anyone_edits_it(self):
        body = main_of(self.client.get(reverse('home')))

        self.assertIn(SHIPPED_HEADING, body)
        self.assertIn(SHIPPED_WELCOME, body)
        self.assertIn('How It Works', body)
        for step in ('Browse', 'Bid', 'Win &amp; Enjoy'):
            with self.subTest(step=step):
                self.assertIn(step, body)

    def test_the_step_icons_survive(self):
        body = main_of(self.client.get(reverse('home')))

        for icon in ('fas fa-search', 'fas fa-gavel', 'fas fa-check-circle'):
            with self.subTest(icon=icon):
                self.assertIn(icon, body)


class EditingTests(TestCase):
    def test_editing_the_welcome_changes_the_homepage(self):
        page = SitePage.objects.get(slug='home')
        page.title = 'Welcome to the Prairie Daylily Club'
        page.body = '<p>We meet on Tuesdays.</p>'
        page.save()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('Welcome to the Prairie Daylily Club', body)
        self.assertIn('We meet on Tuesdays.', body)
        self.assertNotIn(SHIPPED_WELCOME, body)

    def test_the_welcome_heading_is_the_pages_h1(self):
        page = SitePage.objects.get(slug='home')
        page.title = 'A New Heading'
        page.save()

        html = self.client.get(reverse('home')).content.decode()
        heading = html[html.index('<h1'):html.index('</h1>')]

        self.assertIn('A New Heading', heading)

    def test_markup_in_the_welcome_body_is_honoured(self):
        """It is admin-authored HTML, the same as the other site pages."""
        page = SitePage.objects.get(slug='home')
        page.body = '<p>One</p><ul><li>Two</li></ul>'
        page.save()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('<li>Two</li>', body)

    def test_an_address_in_the_welcome_is_protected(self):
        """Same treatment as every other admin-authored block."""
        page = SitePage.objects.get(slug='home')
        page.body = '<p>Write to club@example.org.</p>'
        page.save()

        response = self.client.get(reverse('home'))

        self.assertNotContains(response, 'club@example.org')
        self.assertContains(response, 'data-pe=')

    def test_renaming_the_steps_heading_works(self):
        page = SitePage.objects.get(slug='home-how-it-works')
        page.title = 'Three Easy Steps'
        page.save()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('Three Easy Steps', body)
        self.assertNotIn('How It Works', body)

    def test_a_lead_paragraph_under_the_steps_heading_shows(self):
        page = SitePage.objects.get(slug='home-how-it-works')
        page.body = '<p>It could not be simpler.</p>'
        page.save()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('It could not be simpler.', body)

    def test_editing_a_step_changes_the_homepage(self):
        step = HomePageStep.objects.get(title='Browse')
        step.title = 'Have a Look'
        step.body = 'Start with the sidebar.'
        step.save()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('Have a Look', body)
        self.assertIn('Start with the sidebar.', body)

    def test_unpublishing_a_step_removes_it(self):
        HomePageStep.objects.filter(title='Bid').update(is_published=False)

        body = main_of(self.client.get(reverse('home')))

        self.assertNotIn('The highest bid wins', body)
        self.assertIn('Browse', body)

    def test_reordering_steps_reorders_the_homepage(self):
        HomePageStep.objects.filter(title='Browse').update(display_order=99)

        body = main_of(self.client.get(reverse('home')))

        self.assertGreater(body.index('>Browse<'), body.index('>Bid<'))

    def test_a_fourth_step_is_laid_out_rather_than_overflowing(self):
        """The row is sized from how many are published, not fixed at three."""
        HomePageStep.objects.create(
            icon='fas fa-truck', title='Ship', body='It arrives.',
            display_order=40,
        )

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('Ship', body)
        self.assertIn('col-md-3', body)
        self.assertNotIn('col-md-4 text-center', body)

    def test_two_steps_share_the_row(self):
        HomePageStep.objects.filter(title='Win & Enjoy').delete()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('col-md-6', body)

    def test_a_step_with_no_icon_renders_without_one(self):
        HomePageStep.objects.filter(title='Browse').update(icon='')

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('Browse', body)
        self.assertNotIn('fas fa-search', body)

    def test_step_text_is_escaped(self):
        HomePageStep.objects.filter(title='Browse').update(
            body='<script>alert(1)</script>'
        )

        response = self.client.get(reverse('home'))

        self.assertNotContains(response, '<script>alert(1)</script>')


class MissingContentTests(TestCase):
    """What a fresh or half-restored database renders."""

    def test_a_missing_welcome_page_falls_back_to_the_shipped_wording(self):
        SitePage.objects.filter(slug='home').delete()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn(SHIPPED_HEADING, body)
        self.assertIn(SHIPPED_WELCOME, body)

    def test_a_missing_steps_page_falls_back_to_the_shipped_heading(self):
        SitePage.objects.filter(slug='home-how-it-works').delete()

        body = main_of(self.client.get(reverse('home')))

        self.assertIn('How It Works', body)
        self.assertIn('Browse', body)

    def test_with_no_steps_the_section_disappears_rather_than_leaving_a_heading(self):
        HomePageStep.objects.all().delete()

        body = main_of(self.client.get(reverse('home')))

        self.assertNotIn('How It Works', body)
        # The rest of the page is untouched.
        self.assertIn(SHIPPED_HEADING, body)

    def test_an_entirely_empty_database_still_renders_the_homepage(self):
        SitePage.objects.all().delete()
        HomePageStep.objects.all().delete()

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SHIPPED_HEADING)


class FragmentsAreNotPagesTests(TestCase):
    """
    The two homepage rows share a model with Terms and Privacy, which are
    served at /legal/<slug>/. Without this they would also be reachable there,
    serving half the homepage as a bare page at a URL nothing links to.
    """

    def test_the_welcome_fragment_is_not_served_as_its_own_page(self):
        response = self.client.get('/legal/home/')

        self.assertRedirects(response, reverse('home'), status_code=301)

    def test_the_steps_fragment_is_not_served_as_its_own_page(self):
        response = self.client.get('/legal/home-how-it-works/')

        self.assertRedirects(response, reverse('home'), status_code=301)

    def test_real_pages_are_unaffected(self):
        response = self.client.get('/legal/terms-of-service/')

        self.assertEqual(response.status_code, 200)

    def test_the_admin_view_on_site_link_points_at_the_homepage(self):
        page = SitePage.objects.get(slug='home')

        self.assertEqual(page.get_absolute_url(), reverse('home'))

    def test_the_pages_list_says_which_rows_are_homepage_fragments(self):
        staff = User.objects.create_user(
            'pagesboss', 'pagesboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse('admin:auctions_sitepage_changelist'))

        self.assertContains(response, 'On the home page')
        # And a real page still shows its address.
        self.assertContains(response, '/legal/terms-of-service/')


class AdminTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(
            'homeboss', 'homeboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

    def test_the_steps_have_their_own_admin_list(self):
        response = self.client.get(
            reverse('admin:auctions_homepagestep_changelist')
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Browse')

    def test_order_and_publication_are_editable_from_the_list(self):
        response = self.client.get(
            reverse('admin:auctions_homepagestep_changelist')
        )

        self.assertContains(response, 'form-0-display_order')
        self.assertContains(response, 'form-0-is_published')

    def test_the_step_form_says_where_the_other_wording_lives(self):
        step = HomePageStep.objects.get(title='Browse')

        response = self.client.get(
            reverse('admin:auctions_homepagestep_change', args=[step.pk])
        )

        self.assertContains(response, 'Pages')
        self.assertContains(response, 'fontawesome.com/icons')

    def test_saving_a_step_updates_the_homepage(self):
        step = HomePageStep.objects.get(title='Browse')

        self.client.post(
            reverse('admin:auctions_homepagestep_change', args=[step.pk]),
            {
                'title': 'Take a Look',
                'body': 'Start in the sidebar.',
                'icon': 'fas fa-eye',
                'display_order': '10',
                'is_published': 'on',
            },
        )

        body = main_of(self.client.get(reverse('home')))
        self.assertIn('Take a Look', body)
        self.assertIn('fas fa-eye', body)

    def test_the_sidebar_offers_the_steps(self):
        from django.conf import settings

        titles = [
            str(item['title'])
            for group in settings.UNFOLD['SIDEBAR']['navigation']
            for item in group['items']
        ]

        self.assertIn('Home Page Steps', titles)
