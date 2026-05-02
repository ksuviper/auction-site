from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator

from allauth.account.views import LoginView as AllauthLoginView
from django_ratelimit.decorators import ratelimit

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


@method_decorator(
    ratelimit(key='ip', rate='5/m', method='POST', block=False),
    name='post',
)
class RateLimitedLoginView(AllauthLoginView):
    def post(self, request, *args, **kwargs):
        if getattr(request, 'limited', False):
            return HttpResponse(
                'Too many login attempts. Please wait a minute and try again.',
                status=429,
            )
        return super().post(request, *args, **kwargs)
