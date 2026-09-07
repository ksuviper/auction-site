"""Tests for the admin-managed content pages and the redesigned homepage.

Three things here are easy to break without noticing:

  * The privacy policy is a live legal page the Google and Facebook OAuth apps
    were pointed at. It moved from a static template into an editable SitePage,
    so its old address has to keep working and its text has to have survived
    the move — not been replaced with placeholder prose.
  * The homepage's About Us section is read from the same SitePage as /about/,
    so editing one cannot leave the other stale.
  * The "How It Works" copy was moved, not rewritten.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from auctions.models import FAQItem, SitePage

User = get_user_model()


class SeededPageTests(TestCase):
    """Migration 0020 seeds the three pages the site links to."""

    def test_all_three_pages_exist(self):
        slugs = set(SitePage.objects.values_list('slug', flat=True))

        self.assertEqual(
            slugs, {'privacy-policy', 'terms-of-service', 'about-us'}
        )

    def test_the_privacy_policy_kept_its_real_text(self):
        """
        Not a placeholder. This is the policy the OAuth apps link to, and it
        names what is collected and shared — replacing it would take a live
        legal page down.
        """
        body = SitePage.objects.get(slug='privacy-policy').body

        self.assertIn('We do not sell your personal information', body)
        self.assertIn('Social Login', body)
        self.assertIn('asqdaylilies@gmail.com', body)
        self.assertNotIn('placeholder', body.lower())

    def test_the_privacy_policy_still_links_to_data_deletion(self):
        """That link was a template tag; in the database it has to be a path."""
        body = SitePage.objects.get(slug='privacy-policy').body

        self.assertIn(reverse('data_deletion'), body)

    def test_the_other_two_are_marked_as_placeholders(self):
        for slug in ('terms-of-service', 'about-us'):
            with self.subTest(slug=slug):
                body = SitePage.objects.get(slug=slug).body
                self.assertIn('placeholder', body.lower())


class SitePageViewTests(TestCase):
    def test_a_legal_page_renders_its_body(self):
        response = self.client.get(
            reverse('site_page', kwargs={'slug': 'terms-of-service'})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/site_page.html')
        self.assertContains(response, 'Terms of Service')
        self.assertContains(response, 'A bid is a commitment to buy')

    def test_html_in_the_body_is_rendered_not_escaped(self):
        """An admin writes headings and links; escaping them would show tags."""
        SitePage.objects.create(
            slug='markup', title='Markup', body='<h2>A Heading</h2><p>Text.</p>'
        )

        response = self.client.get(
            reverse('site_page', kwargs={'slug': 'markup'})
        )

        self.assertContains(response, '<h2>A Heading</h2>', html=False)
        self.assertNotContains(response, '&lt;h2&gt;')

    def test_an_unknown_slug_is_a_404(self):
        response = self.client.get(
            reverse('site_page', kwargs={'slug': 'no-such-page'})
        )

        self.assertEqual(response.status_code, 404)

    def test_about_us_has_its_own_friendly_url(self):
        response = self.client.get(reverse('about_us'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'About Us')

    def test_about_us_under_legal_redirects_to_the_canonical_url(self):
        """One address per page, so nothing links to the second one."""
        response = self.client.get(
            reverse('site_page', kwargs={'slug': 'about-us'})
        )

        self.assertRedirects(response, reverse('about_us'), status_code=301)

    def test_pages_are_public(self):
        """Legal pages must be readable by someone who cannot log in."""
        for url in (reverse('about_us'),
                    reverse('site_page', kwargs={'slug': 'privacy-policy'})):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class PrivacyPolicyMoveTests(TestCase):
    """The old /privacy/ address is what external services were given."""

    def test_the_old_url_still_resolves(self):
        self.assertEqual(reverse('privacy_policy'), '/privacy/')

    def test_it_redirects_permanently_to_the_editable_page(self):
        response = self.client.get('/privacy/')

        self.assertRedirects(
            response,
            reverse('site_page', kwargs={'slug': 'privacy-policy'}),
            status_code=301,
        )

    def test_following_it_lands_on_the_real_policy(self):
        response = self.client.get('/privacy/', follow=True)

        self.assertContains(response, 'We do not sell your personal information')

    def test_the_data_deletion_page_is_untouched(self):
        """Facebook requires that URL and it documents a fixed procedure."""
        response = self.client.get(reverse('data_deletion'))

        self.assertEqual(response.status_code, 200)


class FAQPageTests(TestCase):
    def setUp(self):
        self.url = reverse('faq')

    def test_it_lists_published_questions_in_order(self):
        FAQItem.objects.create(
            question='Second question?', answer='B', display_order=2
        )
        FAQItem.objects.create(
            question='First question?', answer='A', display_order=1
        )

        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            html.index('First question?'), html.index('Second question?')
        )

    def test_unpublished_questions_are_hidden(self):
        FAQItem.objects.create(
            question='Draft question?', answer='...', is_published=False
        )

        response = self.client.get(self.url)

        self.assertNotContains(response, 'Draft question?')

    def test_an_empty_faq_says_so_rather_than_looking_broken(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'No questions posted yet')

    def test_line_breaks_in_an_answer_are_kept(self):
        """An admin types prose, not markup; their paragraphs should survive."""
        FAQItem.objects.create(
            question='Multi-line?', answer='First line.\nSecond line.'
        )

        response = self.client.get(self.url)

        self.assertContains(response, 'First line.<br>Second line.')

    def test_html_in_an_answer_is_rendered(self):
        FAQItem.objects.create(
            question='Linked?', answer='See <a href="/faq/">the FAQ</a>.'
        )

        response = self.client.get(self.url)

        self.assertContains(response, '<a href="/faq/">the FAQ</a>')

    def test_questions_left_at_the_default_order_are_still_stable(self):
        """A page of rows all at 0 should not come back in database order."""
        FAQItem.objects.create(question='Zebra?', answer='z')
        FAQItem.objects.create(question='Apple?', answer='a')

        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertLess(html.index('Apple?'), html.index('Zebra?'))

    def test_the_faq_is_linked_from_the_navigation(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, reverse('faq'))


class HomepageTests(TestCase):
    def setUp(self):
        self.url = reverse('home')

    def test_active_auctions_is_gone(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, 'Active Auctions')
        self.assertNotIn('listings', response.context)

    def test_how_it_works_is_present_with_the_original_copy(self):
        """Moved, not rewritten — the wording is the client's."""
        response = self.client.get(self.url)

        self.assertContains(response, 'How It Works')
        self.assertContains(
            response,
            'Use the category sidebar to find daylilies by type and seller.',
        )
        self.assertContains(
            response,
            'Place bids on plants you love. The highest bid wins when time runs out.',
        )
        self.assertContains(
            response,
            'Pay the seller directly using their preferred payment method.',
        )

    def test_the_about_section_comes_from_the_editable_page(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'About Us')
        self.assertContains(response, 'brings together growers and')

    def test_editing_the_about_page_updates_the_homepage(self):
        page = SitePage.objects.get(slug='about-us')
        page.body = '<p>We grow daylilies in Kansas.</p>'
        page.save()

        response = self.client.get(self.url)

        self.assertContains(response, 'We grow daylilies in Kansas.')

    def test_a_long_about_page_is_truncated_with_a_read_more_link(self):
        page = SitePage.objects.get(slug='about-us')
        page.body = '<p>' + ' '.join(f'word{i}' for i in range(200)) + '</p>'
        page.save()

        response = self.client.get(self.url)

        self.assertTrue(response.context['about_truncated'])
        self.assertContains(response, reverse('about_us'))
        self.assertContains(response, 'Read more')
        self.assertNotContains(response, 'word199')

    def test_a_short_about_page_offers_no_read_more_link(self):
        page = SitePage.objects.get(slug='about-us')
        page.body = '<p>Short and sweet.</p>'
        page.save()

        response = self.client.get(self.url)

        self.assertFalse(response.context['about_truncated'])
        self.assertNotContains(response, 'Read more')

    def test_the_homepage_survives_the_about_page_being_deleted(self):
        """An admin can delete a SitePage; the homepage must not 500."""
        SitePage.objects.filter(slug='about-us').delete()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'How It Works')

    def test_the_category_sidebar_is_still_there(self):
        """It is the way in to the listings now, so it cannot go with them."""
        response = self.client.get(self.url)

        self.assertContains(response, 'Browse by Category')


class ContentAdminTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(
            'contentboss', 'contentboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)

    def test_the_faq_changelist_allows_inline_reordering(self):
        FAQItem.objects.create(question='Q?', answer='A')

        response = self.client.get(
            reverse('admin:auctions_faqitem_changelist')
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'display_order')
        self.assertContains(response, 'is_published')

    def test_the_site_page_changelist_links_to_the_public_page(self):
        response = self.client.get(
            reverse('admin:auctions_sitepage_changelist')
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/legal/terms-of-service/')
        # About Us links to its own address, not the /legal/ one.
        self.assertContains(response, reverse('about_us'))

    def test_a_site_page_is_editable(self):
        page = SitePage.objects.get(slug='terms-of-service')

        response = self.client.get(
            reverse('admin:auctions_sitepage_change', args=[page.pk])
        )

        self.assertContains(response, 'name="body"')
        self.assertContains(response, 'name="title"')
