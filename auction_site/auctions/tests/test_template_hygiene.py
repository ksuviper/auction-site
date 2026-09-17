"""
Template mistakes that render as visible text rather than failing loudly.

Django's ``{# ... #}`` comment is single-line only. Spread one over two lines
and it stops being a comment: the lexer never matches it, and the developer's
note is served to visitors as page content. Nothing errors, no test fails by
accident, and it is invisible in review because it still looks like a comment.

It reached the live site twice before a test in another module happened to trip
over the phrase "Seller Monthly" appearing where it should not. This sweeps for
it directly.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

TEMPLATE_ROOTS = [
    Path(settings.BASE_DIR) / 'auctions' / 'templates',
    Path(settings.BASE_DIR) / 'auction_site' / 'templates',
]


def template_files():
    for root in TEMPLATE_ROOTS:
        if root.is_dir():
            yield from sorted(root.rglob('*.html'))


class CommentSyntaxTests(TestCase):
    def test_there_are_templates_to_check(self):
        """Otherwise the sweep below would pass by finding nothing."""
        self.assertGreater(len(list(template_files())), 20)

    def test_no_comment_is_left_open_across_lines(self):
        """
        A ``{#`` with no ``#}`` on the same line is not a comment at all — the
        text between them is rendered. ``{% comment %}`` is the multi-line form.
        """
        offenders = []
        for path in template_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if '{#' in line and '#}' not in line:
                    offenders.append(
                        f'{path.relative_to(settings.BASE_DIR)}:{number}: '
                        f'{line.strip()[:60]}'
                    )

        self.assertEqual(
            offenders,
            [],
            'These render as visible page text. Use {% comment %} … '
            '{% endcomment %} for anything spanning more than one line:\n'
            + '\n'.join(offenders),
        )

    def test_every_comment_block_is_closed(self):
        """An unclosed {% comment %} swallows the rest of the page instead."""
        unbalanced = []
        for path in template_files():
            text = path.read_text()
            opened = text.count('{% comment %}')
            closed = text.count('{% endcomment %}')
            if opened != closed:
                unbalanced.append(
                    f'{path.relative_to(settings.BASE_DIR)}: '
                    f'{opened} opened, {closed} closed'
                )

        self.assertEqual(unbalanced, [], '\n'.join(unbalanced))
