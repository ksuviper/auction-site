from auctions.models import AuctionCategory
from auctions.queries import sellers_grouped_by_category


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
