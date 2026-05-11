from django.utils import timezone

from auctions.models import AuctionCategory, Seller


def sidebar(request):
    now = timezone.now()

    categories = AuctionCategory.objects.filter(is_active=True)

    # Show sellers who have at least one active, non-closed listing that hasn't ended
    active_seller_ids = (
        Seller.objects
        .filter(
            auctionlisting__is_active=True,
            auctionlisting__is_closed=False,
            auctionlisting__ends_at__gt=now,
        )
        .values_list('pk', flat=True)
        .distinct()
    )

    current_sellers = (
        Seller.objects
        .filter(pk__in=active_seller_ids)
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
