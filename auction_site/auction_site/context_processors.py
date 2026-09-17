import logging

from auctions.models import AuctionCategory, SiteSettings
from auctions.queries import sellers_grouped_by_category

logger = logging.getLogger(__name__)


def site_settings(request):
    """
    The admin-managed header wording, banner and app icon, for base.html.

    Wrapped because this runs on every render including the error pages: a
    database that is unreachable, or a row missing before migrations have run,
    must give a page with the built-in logo rather than a 500 on top of a 500.

    The fallback is an unsaved instance rather than None. It touches no
    database and carries the fields' own defaults, so a site whose database has
    gone still shows its name and slogan and falls back to the built-in images
    — where None would have left the header blank. That distinction matters
    now that the wording lives in the row: blank has to mean "an admin cleared
    it", not "the query failed".
    """
    try:
        return {'site_settings': SiteSettings.load()}
    except Exception:
        logger.exception('Could not load SiteSettings; falling back to defaults')
        return {'site_settings': SiteSettings()}


def sidebar(request):
    """
    The category → seller navigation shown on every page.

    Sellers are grouped by the category they are assigned to
    (``profile.seller_category``), the same basis the category page uses. It
    used to group them by the category on each of their active listings, which
    put a seller under whichever category their plants happened to carry — so
    the sidebar and the category page could list different people for the same
    category.

    Narrowed to sellers with something open, because this is a "what's on now"
    shortcut; the category page lists the full roster, including anyone between
    listings.
    """
    categories = AuctionCategory.objects.filter(is_active=True)
    sellers = sellers_grouped_by_category(only_with_listings=True)

    return {
        'sidebar_data': [
            {'category': cat, 'sellers': sellers.get(cat.pk, [])}
            for cat in categories
        ]
    }
