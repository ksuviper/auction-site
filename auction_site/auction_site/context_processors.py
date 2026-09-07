from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing
from auctions.utils import seller_display_name


def sidebar(request):
    now = timezone.now()

    categories = AuctionCategory.objects.filter(is_active=True)

    # Get active listings with their sellers, grouped by category
    active_listings = (
        AuctionListing.objects
        .filter(is_active=True, is_closed=False, ends_at__gt=now)
        .select_related('seller__profile', 'category')
    )

    # Build {category_id: {seller pk: seller user}}
    sellers_by_cat = {}
    for listing in active_listings:
        sellers_by_cat.setdefault(listing.category_id, {})
        sellers_by_cat[listing.category_id][listing.seller_id] = listing.seller

    return {
        'sidebar_data': [
            {
                'category': cat,
                'sellers': sorted(
                    sellers_by_cat.get(cat.pk, {}).values(),
                    key=seller_display_name,
                ),
            }
            for cat in categories
        ]
    }
