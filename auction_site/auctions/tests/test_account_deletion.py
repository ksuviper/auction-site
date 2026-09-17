"""
What may be deleted, and what the club's records keep.

Three rules:

1. A profile is never deleted on its own. It belongs to an account, and the
   rest of the code reads ``user.profile`` without checking it exists.
2. An account that has traded is not deleted at all. Listings and invoices were
   already PROTECT, so the database refused those. The ones that needed
   catching are the quiet relations — bids, automatic bids, comments and
   membership records cascade, and a won auction is SET_NULL — so deleting a
   member used to erase their bidding history in silence.
3. An account with no history still deletes, and takes its profile with it.

Rule 3 is the one most easily broken by enforcing the first two, so it is
tested from both ends: the cascade still runs, and the guard on the profile
does not stand in its way.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    ListingComment,
    ProfileDeletionNotAllowed,
    ProxyBid,
    Subscription,
    UserProfile,
    Wishlist,
    describe_user_activity,
    user_activity,
)
from auctions.tests.utils import make_seller

User = get_user_model()


class ProfileIsNotDeletableAloneTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user('lone', 'lone@example.com', 'pw')

    def test_deleting_a_profile_directly_is_refused(self):
        with self.assertRaises(ProfileDeletionNotAllowed):
            self.member.profile.delete()

        self.assertTrue(UserProfile.objects.filter(user=self.member).exists())

    def test_the_refusal_explains_what_to_do_instead(self):
        with self.assertRaises(ProfileDeletionNotAllowed) as caught:
            self.member.profile.delete()

        self.assertIn('Delete the user instead', str(caught.exception))


class DeletingAnAccountTests(TestCase):
    """Rule 3. The cascade has to survive the guards put in for 1 and 2."""

    def test_a_user_with_no_history_can_be_deleted(self):
        member = User.objects.create_user('quiet', 'quiet@example.com', 'pw')
        profile_pk = member.profile.pk

        member.delete()

        self.assertFalse(User.objects.filter(username='quiet').exists())
        self.assertFalse(UserProfile.objects.filter(pk=profile_pk).exists())

    def test_the_profile_guard_does_not_block_the_cascade(self):
        """
        Cascades are collected and deleted in SQL rather than by calling each
        object's delete(), which is the only reason rule 1 and rule 3 can both
        hold. If that ever changed this test fails rather than the site.
        """
        member = User.objects.create_user('cascade', 'cascade@example.com', 'pw')

        member.delete()  # must not raise ProfileDeletionNotAllowed

        self.assertEqual(UserProfile.objects.filter(user_id=member.pk).count(), 0)

    def test_things_that_belong_to_the_account_go_with_it(self):
        member = User.objects.create_user('tidy', 'tidy@example.com', 'pw')
        Wishlist.objects.create(user=member, listing_title_keyword='spider')

        member.delete()

        self.assertEqual(Wishlist.objects.filter(user_id=member.pk).count(), 0)


class WhatCountsAsHistoryTests(TestCase):
    def setUp(self):
        self.category = AuctionCategory.objects.create(name='Spiders')
        self.seller = make_seller('histseller', first_name='H', last_name='S')
        self.member = User.objects.create_user('active', 'active@example.com', 'pw')

    def _listing(self, **kwargs):
        now = timezone.now()
        return AuctionListing.objects.create(
            title='A plant',
            category=self.category,
            seller=self.seller,
            start_price='10.00',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5),
            is_active=True,
            **kwargs,
        )

    def test_a_fresh_account_has_no_history(self):
        self.assertEqual(user_activity(self.member), [])
        self.assertEqual(describe_user_activity(self.member), '')

    def test_a_bid_counts(self):
        Bid.objects.create(
            listing=self._listing(), bidder=self.member, amount='11.00'
        )

        self.assertEqual(user_activity(self.member), [('bid', 1)])

    def test_an_automatic_bid_counts(self):
        ProxyBid.objects.create(
            listing=self._listing(), bidder=self.member, max_amount='40.00'
        )

        self.assertEqual(user_activity(self.member), [('automatic bid', 1)])

    def test_a_comment_counts(self):
        ListingComment.objects.create(
            listing=self._listing(), author=self.member, body='Is it fragrant?'
        )

        self.assertEqual(user_activity(self.member), [('comment', 1)])

    def test_a_membership_record_counts(self):
        Subscription.objects.create(
            user=self.member, plan='monthly', status='cancelled'
        )

        self.assertEqual(user_activity(self.member), [('membership record', 1)])

    def test_a_won_auction_counts(self):
        """SET_NULL, so the winner would have been blanked in silence."""
        self._listing(winner=self.member)

        self.assertEqual(user_activity(self.member), [('won auction', 1)])

    def test_listings_count_for_the_seller(self):
        self._listing()

        self.assertEqual(user_activity(self.seller), [('listing as seller', 1)])

    def test_a_wishlist_does_not_count(self):
        """A saved search belongs to the person, not to the club's records."""
        Wishlist.objects.create(user=self.member, listing_title_keyword='spider')

        self.assertEqual(user_activity(self.member), [])

    def test_several_kinds_are_all_reported(self):
        listing = self._listing()
        Bid.objects.create(listing=listing, bidder=self.member, amount='11.00')
        Bid.objects.create(listing=listing, bidder=self.member, amount='12.00')
        ListingComment.objects.create(
            listing=listing, author=self.member, body='Lovely'
        )

        self.assertEqual(
            user_activity(self.member), [('bid', 2), ('comment', 1)]
        )
        self.assertEqual(describe_user_activity(self.member), '2 bids and 1 comment')

    def test_the_description_reads_as_a_sentence(self):
        listing = self._listing()
        Bid.objects.create(listing=listing, bidder=self.member, amount='11.00')

        self.assertEqual(describe_user_activity(self.member), '1 bid')

    def test_a_missing_user_is_handled(self):
        self.assertEqual(user_activity(None), [])


class AdminRefusalTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'delboss', 'delboss@example.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)
        self.category = AuctionCategory.objects.create(name='Spiders')
        self.seller = make_seller('admseller', first_name='A', last_name='S')
        self.member = User.objects.create_user('bidder', 'bidder@example.com', 'pw')
        now = timezone.now()
        self.listing = AuctionListing.objects.create(
            title='A plant', category=self.category, seller=self.seller,
            start_price='10.00', starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5), is_active=True,
        )

    def _bid(self):
        Bid.objects.create(
            listing=self.listing, bidder=self.member, amount='11.00'
        )

    # ── profiles ────────────────────────────────────────────────────────────

    def test_the_profile_add_page_is_gone(self):
        response = self.client.get(reverse('admin:auctions_userprofile_add'))

        self.assertEqual(response.status_code, 403)

    def test_the_profile_changelist_offers_no_delete_action(self):
        response = self.client.get(
            reverse('admin:auctions_userprofile_changelist')
        )

        self.assertNotContains(response, 'delete_selected')

    def test_deleting_a_profile_by_url_redirects_to_the_account(self):
        response = self.client.get(
            reverse(
                'admin:auctions_userprofile_delete',
                args=[self.member.profile.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse('admin:auth_user_change', args=[self.member.pk]),
        )
        self.assertTrue(UserProfile.objects.filter(user=self.member).exists())

    def test_the_profile_refusal_says_why(self):
        response = self.client.get(
            reverse(
                'admin:auctions_userprofile_delete',
                args=[self.member.profile.pk],
            ),
            follow=True,
        )

        self.assertContains(response, 'belongs to the account')

    # ── accounts ────────────────────────────────────────────────────────────

    def test_an_account_with_a_bid_cannot_be_deleted(self):
        self._bid()

        response = self.client.post(
            reverse('admin:auth_user_delete', args=[self.member.pk]),
            {'post': 'yes'},
            follow=True,
        )

        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.assertContains(response, 'cannot be deleted')
        self.assertContains(response, '1 bid')

    def test_the_delete_button_is_hidden_for_such_an_account(self):
        self._bid()

        response = self.client.get(
            reverse('admin:auth_user_change', args=[self.member.pk])
        )

        self.assertNotContains(response, 'Delete user')

    def test_a_bid_is_not_destroyed_by_an_attempted_delete(self):
        """The whole point: the history had been cascading away silently."""
        self._bid()

        self.client.post(
            reverse('admin:auth_user_delete', args=[self.member.pk]),
            {'post': 'yes'},
        )

        self.assertEqual(Bid.objects.filter(bidder=self.member).count(), 1)

    def test_the_bulk_action_also_refuses(self):
        self._bid()

        self.client.post(
            reverse('admin:auth_user_changelist'),
            {
                'action': 'delete_selected',
                '_selected_action': [str(self.member.pk)],
                'post': 'yes',
            },
            follow=True,
        )

        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.assertEqual(Bid.objects.filter(bidder=self.member).count(), 1)

    def test_a_seller_with_listings_still_cannot_be_deleted(self):
        """Already PROTECT, but it should refuse through the same door now."""
        response = self.client.post(
            reverse('admin:auth_user_delete', args=[self.seller.pk]),
            {'post': 'yes'},
            follow=True,
        )

        self.assertTrue(User.objects.filter(pk=self.seller.pk).exists())
        self.assertContains(response, 'cannot be deleted')

    def test_an_account_with_no_history_is_still_deletable_from_the_admin(self):
        """The rule must not turn into "nobody can ever be removed"."""
        spare = User.objects.create_user('spare', 'spare@example.com', 'pw')
        profile_pk = spare.profile.pk

        response = self.client.get(
            reverse('admin:auth_user_change', args=[spare.pk])
        )
        self.assertContains(response, 'Delete')

        self.client.post(
            reverse('admin:auth_user_delete', args=[spare.pk]), {'post': 'yes'}
        )

        self.assertFalse(User.objects.filter(pk=spare.pk).exists())
        self.assertFalse(UserProfile.objects.filter(pk=profile_pk).exists())
