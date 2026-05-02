from django.shortcuts import render
from django.utils import timezone

from auctions.models import AuctionListing


def index(request):
    now = timezone.now()
    listings = (
        AuctionListing.objects
        .filter(is_active=True, is_closed=False, ends_at__gt=now)
        .select_related('category', 'seller')
        .order_by('ends_at')
    )
    return render(request, 'index.html', {'listings': listings})
