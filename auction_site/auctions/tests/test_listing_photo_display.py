"""
The listing detail page shows the whole photo; the grid still crops to fit.

The detail image used to be a fixed 420px box with ``object-fit: cover``, which
fills the box and discards whatever will not fit — so a tall plant lost its
bloom or its base. That is fine on the browse grid, where uniform thumbnails
keep the cards aligned, and wrong on the page where somebody decides whether to
bid.

These read the stylesheet, which is unusual for a test, but the bug was in CSS
and nothing else can catch it coming back. The grid assertions matter as much as
the detail ones: the brief was explicit that the browse page works correctly and
must not be touched.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

STYLESHEET = Path(settings.BASE_DIR) / 'auction_site' / 'static' / 'style.css'


def rules_for(selector):
    """Every declaration block for a selector, including inside media queries."""
    css = STYLESHEET.read_text()
    pattern = re.compile(
        r'(?:^|[,{}\s])' + re.escape(selector) + r'\s*(?:,[^{]*)?\{([^}]*)\}',
        re.MULTILINE,
    )
    return [match.group(1) for match in pattern.finditer(css)]


def declarations(selector):
    """Those blocks flattened into a {property: value} mapping."""
    found = {}
    for block in rules_for(selector):
        for line in block.split(';'):
            if ':' in line:
                prop, _, value = line.partition(':')
                found[prop.strip()] = value.strip()
    return found


class StylesheetExistsTests(TestCase):
    def test_the_stylesheet_is_where_these_tests_look(self):
        """Otherwise every assertion below would pass against an empty file."""
        self.assertTrue(STYLESHEET.is_file(), f'no stylesheet at {STYLESHEET}')
        self.assertTrue(rules_for('.listing-detail-img'))
        self.assertTrue(rules_for('.listing-card-img'))


class DetailPhotoTests(TestCase):
    def setUp(self):
        self.rules = declarations('.listing-detail-img')

    def test_the_photo_is_never_cropped(self):
        """object-fit: cover is the bug. Anything that fits inside is fine."""
        self.assertNotEqual(self.rules.get('object-fit'), 'cover')
        self.assertEqual(self.rules.get('object-fit'), 'contain')

    def test_no_fixed_height_forces_a_shape_on_the_photo(self):
        """
        A hard height is what made cropping necessary in the first place. Caps
        are fine — they stop a tall photo pushing the bid panel off screen —
        but the height itself has to follow the image.
        """
        height = self.rules.get('height')

        self.assertEqual(height, 'auto', f'height is pinned to {height!r}')
        self.assertNotIn('height: 420px', ' '.join(rules_for('.listing-detail-img')))

    def test_it_is_capped_so_a_tall_photo_does_not_fill_the_screen(self):
        self.assertIn('max-height', self.rules)
        self.assertIn('max-width', self.rules)

    def test_it_stays_inside_its_column(self):
        self.assertEqual(self.rules.get('max-width'), '100%')

    def test_a_narrow_photo_is_centred_rather_than_left_adrift(self):
        self.assertEqual(self.rules.get('margin-inline'), 'auto')


class GridIsUntouchedTests(TestCase):
    """
    The browse grid was confirmed working and explicitly out of scope. Uniform
    thumbnails are what keep the cards aligned, so cropping there is deliberate.
    """

    def setUp(self):
        self.rules = declarations('.listing-card-img')

    def test_the_cards_still_crop_to_a_uniform_shape(self):
        self.assertEqual(self.rules.get('object-fit'), 'cover')

    def test_the_cards_still_have_their_fixed_height(self):
        self.assertEqual(self.rules.get('height'), '200px')

    def test_the_detail_fix_did_not_leak_into_the_cards(self):
        self.assertNotIn('max-height', self.rules)


class TemplateTests(TestCase):
    """The no-photo box cannot share the photo's class any more."""

    def setUp(self):
        self.markup = (
            Path(settings.BASE_DIR)
            / 'auctions' / 'templates' / 'auctions' / 'listing_detail.html'
        ).read_text()

    def test_the_photo_uses_the_detail_image_class(self):
        self.assertIn('class="listing-detail-img', self.markup)

    def test_the_no_photo_box_has_a_class_of_its_own(self):
        """
        It is an empty box holding a centred icon. Sharing a class that now
        sizes itself to its contents would collapse it to the icon's height.
        """
        self.assertIn('listing-detail-placeholder', self.markup)

    def test_the_placeholder_keeps_a_height(self):
        self.assertIn('height', declarations('.listing-detail-placeholder'))
