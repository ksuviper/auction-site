"""
The published contact address must not appear in any page a harvester reads.

Two things have to hold at once, and testing only the first is how this kind of
change quietly stops working:

1. No page carries the address in a form a bot pattern-matches — no `mailto:`
   in an href, no `user@host` anywhere in the body.
2. A visitor can still get the address. It is recoverable from the page, and
   the no-JavaScript fallback is readable text rather than a dead link.

The address is checked for as a literal string, so a future template that
hardcodes it again fails here rather than on the live site.
"""

import base64

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse

from auctions.models import FAQItem, SitePage
from auctions.templatetags.contact_tags import pack, protect_emails, unpack

User = get_user_model()

ADDRESS = settings.CONTACT_EMAIL


def render(template_string, **context):
    return Template(template_string).render(Context(context))


class PackingTests(TestCase):
    def test_packing_round_trips(self):
        self.assertEqual(unpack(pack(ADDRESS)), ADDRESS)

    def test_the_packed_form_is_not_the_address(self):
        self.assertNotIn(ADDRESS, pack(ADDRESS))
        self.assertNotIn('@', pack(ADDRESS))

    def test_a_plain_base64_decode_does_not_yield_the_address(self):
        """
        The reversal earns its place here.

        A harvester that base64-decodes every attribute it sees gets a string
        that still is not an address, so the result does not survive its own
        validation step.
        """
        naive = base64.b64decode(pack(ADDRESS).encode()).decode()

        self.assertNotEqual(naive, ADDRESS)
        self.assertNotIn('@' + ADDRESS.partition('@')[2], naive)


class TagTests(TestCase):
    def test_the_tag_emits_no_address_and_no_mailto(self):
        html = render('{% load contact_tags %}{% protected_email %}')

        self.assertNotIn(ADDRESS, html)
        self.assertNotIn('mailto:', html)

    def test_the_address_is_recoverable_from_what_it_emits(self):
        html = render('{% load contact_tags %}{% protected_email %}')

        packed = html.split('data-pe="')[1].split('"')[0]
        self.assertEqual(unpack(packed), ADDRESS)

    def test_the_fallback_is_readable_text_not_a_dead_link(self):
        """
        Without JavaScript a visitor must still be able to read the address.

        An anchor with a placeholder href would look clickable and do nothing,
        which is worse than plain text.
        """
        html = render('{% load contact_tags %}{% protected_email %}')

        local, _, domain = ADDRESS.partition('@')
        self.assertIn(f'{local} at {domain.replace(".", " dot ")}', html)
        self.assertNotIn('<a ', html)
        self.assertNotIn('href', html)

    def test_an_explicit_address_is_used_over_the_site_default(self):
        html = render(
            '{% load contact_tags %}{% protected_email "other@example.org" %}'
        )

        packed = html.split('data-pe="')[1].split('"')[0]
        self.assertEqual(unpack(packed), 'other@example.org')

    def test_a_label_replaces_the_spelled_out_fallback(self):
        html = render(
            '{% load contact_tags %}{% protected_email label="Email the club" %}'
        )

        self.assertIn('Email the club', html)
        self.assertNotIn(' at ', html)

    def test_a_label_is_escaped(self):
        html = render(
            '{% load contact_tags %}{% protected_email label=evil %}',
            evil='<script>alert(1)</script>',
        )

        self.assertNotIn('<script>', html)


class FilterTests(TestCase):
    """Admin-typed content, where an address can arrive in either form."""

    def test_a_bare_address_in_prose_is_protected(self):
        out = protect_emails(f'<p>Write to {ADDRESS} any time.</p>')

        self.assertNotIn(ADDRESS, out)
        self.assertIn('data-pe=', out)
        self.assertIn('any time.', out)

    def test_a_mailto_anchor_is_replaced_whole(self):
        """
        Rewriting only the anchor text would leave the address in the href,
        which is the exact string a harvester reads first.
        """
        out = protect_emails(f'<a href="mailto:{ADDRESS}">{ADDRESS}</a>')

        self.assertNotIn('mailto:', out)
        self.assertNotIn(ADDRESS, out)
        self.assertNotIn('<a ', out)

    def test_an_anchor_with_real_wording_keeps_it(self):
        out = protect_emails(f'<a href="mailto:{ADDRESS}">email the club</a>')

        self.assertIn('email the club', out)
        self.assertNotIn(ADDRESS, out)

    def test_other_markup_passes_through_untouched(self):
        body = '<h2>Contact</h2><ul><li>Post</li></ul><a href="/privacy/">Privacy</a>'

        self.assertEqual(protect_emails(body), body)

    def test_empty_content_is_handled(self):
        self.assertEqual(protect_emails(''), '')
        self.assertEqual(protect_emails(None), '')

    def test_several_addresses_in_one_body(self):
        out = protect_emails(f'<p>{ADDRESS}</p><p>someone@example.org</p>')

        self.assertNotIn(ADDRESS, out)
        self.assertNotIn('someone@example.org', out)
        self.assertEqual(out.count('data-pe='), 2)


class PublicPageTests(TestCase):
    """The pages a crawler actually fetches."""

    def setUp(self):
        FAQItem.objects.create(
            question='How do I contact you?',
            answer=f'Email <a href="mailto:{ADDRESS}">{ADDRESS}</a> any time.',
            display_order=1,
            is_published=True,
        )

    def assert_clean(self, url):
        body = self.client.get(url).content.decode()

        self.assertNotIn(ADDRESS, body, f'{url} leaks the address')
        self.assertNotIn('mailto:', body, f'{url} leaks a mailto link')
        return body

    def test_the_faq_page_leaks_neither_the_address_nor_a_mailto(self):
        body = self.assert_clean(reverse('faq'))

        # Still tells the visitor how to reach us.
        self.assertIn('data-pe=', body)

    def test_an_address_inside_an_faq_answer_is_protected_too(self):
        """
        The heading address and the admin-typed one are separate exposures.
        Fixing only the template would leave this one in the page.
        """
        body = self.client.get(reverse('faq')).content.decode()

        answer = body.split('How do I contact you?')[1]
        self.assertNotIn(ADDRESS, answer)
        self.assertIn('data-pe=', answer)

    def test_the_faq_page_still_shows_the_answer_text(self):
        body = self.client.get(reverse('faq')).content.decode()

        self.assertIn('any time.', body)

    def test_the_decoder_script_is_present(self):
        body = self.client.get(reverse('faq')).content.decode()

        self.assertIn('js/protected-email.js', body)

    def test_the_site_pages_are_clean(self):
        SitePage.objects.update_or_create(
            slug='terms-of-service',
            defaults={
                'title': 'Terms',
                'body': f'<p>Email us at <a href="mailto:{ADDRESS}">{ADDRESS}</a>.</p>',
            },
        )

        self.assert_clean(reverse('site_page', kwargs={'slug': 'terms-of-service'}))

    def test_the_about_excerpt_on_the_homepage_is_clean(self):
        SitePage.objects.update_or_create(
            slug='about-us',
            defaults={
                'title': 'About Us',
                'body': f'<p>We are a club. Write to {ADDRESS}.</p>',
            },
        )

        self.assert_clean(reverse('home'))

    def test_the_data_deletion_page_is_clean(self):
        self.assert_clean(reverse('data_deletion'))

    def test_the_pending_approval_page_is_clean(self):
        user = User.objects.create_user('waiting', 'waiting@example.com', 'pw')
        user.profile.is_approved = False
        user.profile.save(update_fields=['is_approved'])
        self.client.force_login(user)

        self.assert_clean(reverse('account_pending_approval'))

    def test_no_public_page_hardcodes_the_address_any_more(self):
        """A sweep, so a new template cannot reintroduce it unnoticed."""
        urls = [
            reverse('home'),
            reverse('faq'),
            reverse('about_us'),
            reverse('data_deletion'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assert_clean(url)


@override_settings(CONTACT_EMAIL='someone.else@example.net')
class ConfiguredAddressTests(TestCase):
    def test_the_published_address_comes_from_settings(self):
        """Changing it is one setting, not a sweep through the templates."""
        body = self.client.get(reverse('faq')).content.decode()

        packed = body.split('data-pe="')[1].split('"')[0]
        self.assertEqual(unpack(packed), 'someone.else@example.net')
