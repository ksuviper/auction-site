"""Tests for browsing: a category lists sellers, a seller lists their plants.

The change these pin down is that a category page is a directory of growers,
not a wall of plants. Two consequences are easy to get wrong:

  * Sellers appear under the category their *profile* is assigned to, not the
    category on each of their listings. The sidebar was previously derived from
    listings, so the two could name different people for the same category.
  * A seller assigned to a category but between listings still belongs on the
    category page — they are featured, they just have nothing open this minute.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing, UserProfile
from auctions.queries import (
    active_listings,
    sellers_grouped_by_category,
    sellers_in_category,
)
from auctions.tests.utils import make_seller

User = get_user_model()


class BrowsingTestCase(TestCase):
    def setUp(self):
        self.spiders = AuctionCategory.objects.create(name='Spiders')
        self.doubles = AuctionCategory.objects.create(name='Doubles')
        self.url = reverse('category_listings', kwargs={'slug': self.spiders.slug})

    def _seller(self, username, category, **kwargs):
        return make_seller(username, category=category, **kwargs)

    def _listing(self, seller, category=None, *, title='Plant', ended=False, **kw):
        now = timezone.now()
        defaults = dict(
            title=title,
            category=category or self.spiders,
            seller=seller,
            start_price='10.00',
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1) if ended else now + timedelta(days=5),
            is_active=not ended,
            is_closed=ended,
        )
        defaults.update(kw)
        return AuctionListing.objects.create(**defaults)


class CategoryPageTests(BrowsingTestCase):
    def test_it_lists_the_sellers_in_the_category(self):
        self._seller('rowan', self.spiders, first_name='Rowan', last_name='Fields')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rowan Fields')
        self.assertContains(response, '1 seller')

    def test_it_does_not_list_the_plants_themselves(self):
        seller = self._seller('rowan', self.spiders)
        self._listing(seller, title='Spider Ballerina')

        response = self.client.get(self.url)

        self.assertNotContains(response, 'Spider Ballerina')
        self.assertNotIn('listings', response.context)

    def test_it_links_through_to_each_sellers_page(self):
        seller = self._seller('rowan', self.spiders)

        response = self.client.get(self.url)

        self.assertContains(
            response, reverse('seller_listings', kwargs={'pk': seller.pk})
        )

    def test_it_shows_a_bio_snippet(self):
        seller = self._seller('rowan', self.spiders)
        UserProfile.objects.filter(user=seller).update(
            seller_bio='Grows unusual spiders and unusual forms.'
        )

        response = self.client.get(self.url)

        self.assertContains(response, 'Grows unusual spiders')

    def test_it_counts_each_sellers_open_listings(self):
        seller = self._seller('rowan', self.spiders)
        self._listing(seller, title='One')
        self._listing(seller, title='Two')
        self._listing(seller, title='Old', ended=True)

        response = self.client.get(self.url)

        self.assertEqual(response.context['sellers'][0].active_listing_count, 2)
        self.assertContains(response, '2 open listings')

    def test_a_seller_between_listings_is_still_listed(self):
        self._seller('rowan', self.spiders, first_name='Rowan')

        response = self.client.get(self.url)

        self.assertContains(response, 'Rowan')
        self.assertContains(response, 'Nothing open right now')

    def test_sellers_with_something_open_are_listed_first(self):
        """
        Otherwise the directory sorts purely by name and can open with a card
        reading "Nothing open right now" — the least useful entry on the page.
        """
        self._seller('aaron', self.spiders, first_name='Aaron')
        busy = self._seller('zora', self.spiders, first_name='Zora')
        self._listing(busy)

        response = self.client.get(self.url)

        self.assertEqual(
            [s.first_name for s in response.context['sellers']],
            ['Zora', 'Aaron'],
        )

    def test_sellers_with_the_same_status_stay_in_name_order(self):
        """Having more open listings does not buy a higher place."""
        aaron = self._seller('aaron', self.spiders, first_name='Aaron')
        zora = self._seller('zora', self.spiders, first_name='Zora')
        self._listing(aaron, title='One')
        for title in ('Two', 'Three', 'Four'):
            self._listing(zora, title=title)

        response = self.client.get(self.url)

        self.assertEqual(
            [s.first_name for s in response.context['sellers']],
            ['Aaron', 'Zora'],
        )

    def test_sellers_from_another_category_are_excluded(self):
        self._seller('rowan', self.spiders, first_name='Rowan')
        self._seller('mallory', self.doubles, first_name='Mallory')

        response = self.client.get(self.url)

        self.assertContains(response, 'Rowan')
        self.assertNotContains(response, 'Mallory')

    def test_a_seller_with_no_category_appears_on_no_category_page(self):
        self._seller('drifter', None, first_name='Drifter')

        response = self.client.get(self.url)

        self.assertNotContains(response, 'Drifter')

    def test_a_non_seller_account_is_never_listed(self):
        buyer = User.objects.create_user('cass', 'cass@example.com', 'pw')
        UserProfile.objects.filter(user=buyer).update(seller_category=self.spiders)

        response = self.client.get(self.url)

        self.assertNotContains(response, 'cass')

    def test_an_empty_category_says_so_plainly(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'No sellers in this category right now')

    def test_an_inactive_category_is_not_browsable(self):
        self.spiders.is_active = False
        self.spiders.save()

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_sellers_page_shows_their_plants(self):
        """The click-through: the category page picks a grower, their page has the plants."""
        seller = self._seller('rowan', self.spiders, first_name='Rowan')
        self._listing(seller, title='Spider Ballerina')

        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': seller.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Spider Ballerina')

    def test_a_sellers_page_shows_plants_from_every_category(self):
        """
        A seller's own page is not filtered by their assigned category.

        Their category decides where they are found; it does not hide a plant
        of theirs that happens to be classed differently.
        """
        seller = self._seller('rowan', self.spiders)
        self._listing(seller, self.spiders, title='A Spider')
        self._listing(seller, self.doubles, title='A Double')

        response = self.client.get(
            reverse('seller_listings', kwargs={'pk': seller.pk})
        )

        self.assertContains(response, 'A Spider')
        self.assertContains(response, 'A Double')


class SidebarTests(BrowsingTestCase):
    """
    The sidebar renders on every page and must agree with the category page.

    It narrows to sellers with something open — it is a "what's on now"
    shortcut — but groups them the same way, by assigned category.
    """

    def test_it_groups_sellers_by_their_assigned_category(self):
        seller = self._seller('rowan', self.spiders, first_name='Rowan')
        self._listing(seller)

        response = self.client.get(reverse('home'))
        data = {row['category'].name: row['sellers'] for row in
                response.context['sidebar_data']}

        self.assertEqual([s.pk for s in data['Spiders']], [seller.pk])
        self.assertEqual(list(data['Doubles']), [])

    def test_a_plant_classed_elsewhere_does_not_move_its_seller(self):
        """
        The old sidebar grouped by listing category, so this seller would have
        shown up under Doubles — where the category page would not list them.
        """
        seller = self._seller('rowan', self.spiders, first_name='Rowan')
        self._listing(seller, self.doubles, title='A Double')

        response = self.client.get(reverse('home'))
        data = {row['category'].name: [s.pk for s in row['sellers']]
                for row in response.context['sidebar_data']}

        self.assertEqual(data['Spiders'], [seller.pk])
        self.assertEqual(data['Doubles'], [])

    def test_it_omits_sellers_with_nothing_open(self):
        self._seller('rowan', self.spiders, first_name='Rowan')

        response = self.client.get(reverse('home'))
        data = {row['category'].name: list(row['sellers']) for row in
                response.context['sidebar_data']}

        self.assertEqual(data['Spiders'], [])

    def test_it_costs_one_query_however_many_categories(self):
        """It renders on every page; per-category queries would multiply up."""
        for name in ('Unusual Forms', 'Miniatures', 'Polymerous'):
            cat = AuctionCategory.objects.create(name=name)
            seller = self._seller(f'seller{cat.pk}', cat)
            self._listing(seller, cat)

        with self.assertNumQueries(1):
            sellers_grouped_by_category(only_with_listings=True)


class QueryHelperTests(BrowsingTestCase):
    """The reusable queries a future browse-all page would be built from."""

    def test_active_listings_with_no_filters_returns_everything_for_sale(self):
        rowan = self._seller('rowan', self.spiders)
        mallory = self._seller('mallory', self.doubles)
        self._listing(rowan, title='Open One')
        self._listing(mallory, self.doubles, title='Open Two')
        self._listing(rowan, title='Closed', ended=True)

        titles = {listing.title for listing in active_listings()}

        self.assertEqual(titles, {'Open One', 'Open Two'})

    def test_active_listings_can_still_filter_by_category(self):
        """Kept working so a per-category listing page is easy to bring back."""
        rowan = self._seller('rowan', self.spiders)
        self._listing(rowan, self.spiders, title='A Spider')
        self._listing(rowan, self.doubles, title='A Double')

        titles = {x.title for x in active_listings(category=self.spiders)}

        self.assertEqual(titles, {'A Spider'})

    def test_active_listings_still_includes_one_scheduled_for_the_future(self):
        """
        starts_at is deliberately not filtered on.

        A listing goes visible when an admin activates it, not when its start
        time arrives — that is how this project behaved before browsing moved
        to seller pages, and the browse queries kept it. Written down because
        it looks like an oversight otherwise.
        """
        now = timezone.now()
        rowan = self._seller('rowan', self.spiders)
        self._listing(
            rowan, title='Future',
            starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=8),
        )

        self.assertEqual([x.title for x in active_listings()], ['Future'])

    def test_sellers_in_category_can_narrow_to_those_with_listings(self):
        busy = self._seller('busy', self.spiders)
        self._seller('idle', self.spiders)
        self._listing(busy)

        pks = [u.pk for u in
               sellers_in_category(self.spiders, only_with_listings=True)]

        self.assertEqual(pks, [busy.pk])
