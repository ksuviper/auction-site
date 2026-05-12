from django.utils import timezone

from auctions.models import AuctionCategory, AuctionListing


def sidebar(request):
    now = timezone.now()

    categories = AuctionCategory.objects.filter(is_active=True)

    # Get active listings with their sellers, grouped by category
    active_listings = (
        AuctionListing.objects
        .filter(is_active=True, is_closed=False, ends_at__gt=now)
        .select_related('seller', 'category')
    )

    # Build {category_id: set of sellers}
    sellers_by_cat = {}
    for listing in active_listings:
        if listing.seller:
            sellers_by_cat.setdefault(listing.category_id, {})
            sellers_by_cat[listing.category_id][listing.seller.pk] = listing.seller

    return {
        'sidebar_data': [
            {
                'category': cat,
                'sellers': sorted(
                    sellers_by_cat.get(cat.pk, {}).values(),
                    key=lambda s: s.name,
                ),
            }
            for cat in categories
        ]
    }
