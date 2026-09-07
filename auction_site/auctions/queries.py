"""
Read-side queries for the public browsing pages.

Browsing goes category → seller → that seller's listings; there is no
"everything on the site" page. The listing-level query lives here anyway rather
than inline in a view, so adding one later is a template and a URL rather than
a rebuilt query — see active_listings().
"""

from django.contrib.auth import get_user_model
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone

from .models import AuctionListing

User = get_user_model()


def active_listings(category=None, seller=None):
    """
    Listings a visitor can act on right now: live, open, not yet ended.

    ``category`` filters on the listing's own category, ``seller`` on its
    seller; both are optional, so with neither this is "everything currently
    for sale" — which is exactly what a future browse-all page needs.

    Note that the category *page* does not use this. It lists the sellers
    assigned to a category, and their pages then show everything they have
    open, whatever category each individual plant carries. A listing's own
    category is what reporting groups by.
    """
    qs = (
        AuctionListing.objects
        .filter(is_active=True, is_closed=False, ends_at__gt=timezone.now())
        .select_related('seller__profile', 'category')
        .order_by('ends_at')
    )
    if category is not None:
        qs = qs.filter(category=category)
    if seller is not None:
        qs = qs.filter(seller=seller)
    return qs


def _seller_directory(*, only_with_listings=False):
    """
    Seller accounts that are assigned to some category, with open-listing counts.

    Shared base for the two callers below. The count is annotated rather than
    counted per seller, so listing a category costs one query however many
    growers are in it.
    """
    now = timezone.now()
    qs = (
        User.objects
        .filter(profile__is_seller=True, profile__seller_category__isnull=False)
        .select_related('profile', 'profile__seller_category')
        .annotate(
            active_listing_count=Count(
                'listings',
                filter=Q(
                    listings__is_active=True,
                    listings__is_closed=False,
                    listings__ends_at__gt=now,
                ),
                distinct=True,
            ),
            # Has anything open, rather than how much: a grower with nine
            # plants is not more worth reading about than one with two, but
            # both beat one with nothing to sell. Without this the directory
            # sorts purely by name, and can open with "Nothing open right now".
            has_open_listings=Case(
                When(active_listing_count__gt=0, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by('-has_open_listings', 'first_name', 'last_name', 'username')
    )
    if only_with_listings:
        qs = qs.filter(active_listing_count__gt=0)
    return qs


def sellers_in_category(category, *, only_with_listings=False):
    """
    The seller accounts assigned to ``category``.

    Assignment is ``profile.seller_category`` — a seller belongs to a category,
    which is what the category page browses by. Each row carries
    ``active_listing_count`` so a page can say how much they have open.

    ``only_with_listings`` narrows to sellers with something open now. The
    sidebar wants that (it is a "what's on" shortcut, and would otherwise grow
    a permanent entry for every seller who ever sold); the category page does
    not, since a featured seller between listings should still be findable.
    """
    return _seller_directory(only_with_listings=only_with_listings).filter(
        profile__seller_category=category
    )


def sellers_grouped_by_category(*, only_with_listings=False):
    """
    ``{category_id: [seller, ...]}`` for every assigned seller, in one query.

    For the sidebar, which renders on every page of the site and would
    otherwise ask per category — cheap individually, but multiplied by every
    category on every request.
    """
    grouped = {}
    for user in _seller_directory(only_with_listings=only_with_listings):
        grouped.setdefault(user.profile.seller_category_id, []).append(user)
    return grouped
