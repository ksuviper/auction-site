from datetime import date, timedelta

from auctions.models import AuctionCategory, Seller


def sidebar(request):
    today = date.today()
    week_start = today - timedelta(days=6)

    categories = AuctionCategory.objects.filter(is_active=True)
    current_sellers = (
        Seller.objects
        .filter(active_week__gte=week_start, active_week__lte=today)
        .select_related('category')
        .order_by('name')
    )

    sellers_by_cat = {}
    for seller in current_sellers:
        sellers_by_cat.setdefault(seller.category_id, []).append(seller)

    return {
        'sidebar_data': [
            {'category': cat, 'sellers': sellers_by_cat.get(cat.pk, [])}
            for cat in categories
        ]
    }
