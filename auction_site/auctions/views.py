from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DetailView, ListView, UpdateView
from django_ratelimit.decorators import ratelimit

from .forms import BidForm, ProfileUpdateForm
from .models import AuctionCategory, AuctionListing, Bid, Seller, UserProfile


# ── Profile views ────────────────────────────────────────────────────────────

class ProfileDetailView(LoginRequiredMixin, DetailView):
    model = UserProfile
    template_name = 'accounts/profile.html'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    form_class = ProfileUpdateForm
    template_name = 'accounts/profile_edit.html'

    def get_object(self, queryset=None):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['next'] = self.request.GET.get('next') or self.request.POST.get('next', '')
        return ctx

    def get_success_url(self):
        next_url = self.request.POST.get('next', '').strip()
        return next_url if next_url else reverse_lazy('profile')


# ── Auction browsing views ────────────────────────────────────────────────────

def _current_week_bounds():
    today = date.today()
    return today - timedelta(days=6), today


class CategoryListingView(ListView):
    template_name = 'auctions/category.html'
    context_object_name = 'listings'

    def get_queryset(self):
        self.category = get_object_or_404(
            AuctionCategory, slug=self.kwargs['slug'], is_active=True
        )
        week_start, today = _current_week_bounds()
        now = timezone.now()
        return (
            AuctionListing.objects
            .filter(
                category=self.category,
                is_active=True,
                is_closed=False,
                ends_at__gt=now,
                seller__active_week__gte=week_start,
                seller__active_week__lte=today,
            )
            .select_related('seller', 'category')
            .order_by('ends_at')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['category'] = self.category
        return ctx


class SellerListingView(ListView):
    template_name = 'auctions/seller_listings.html'
    context_object_name = 'listings'

    def get_queryset(self):
        self.seller = get_object_or_404(Seller, pk=self.kwargs['pk'])
        now = timezone.now()
        return (
            AuctionListing.objects
            .filter(seller=self.seller, is_active=True, is_closed=False, ends_at__gt=now)
            .select_related('category')
            .order_by('ends_at')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['seller'] = self.seller
        return ctx


class ListingDetailView(DetailView):
    model = AuctionListing
    template_name = 'auctions/listing_detail.html'
    context_object_name = 'listing'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        listing = self.object
        now = timezone.now()
        ctx['bids'] = listing.bids.select_related('bidder').order_by('-placed_at')
        ctx['is_ended'] = listing.is_closed or listing.ends_at <= now
        ctx['bid_form'] = BidForm()
        return ctx


# ── Bid submission ────────────────────────────────────────────────────────────

@method_decorator(
    ratelimit(key='user', rate='10/m', method='POST', block=False),
    name='post',
)
class PlaceBidView(LoginRequiredMixin, View):

    def post(self, request, pk):
        if getattr(request, 'limited', False):
            messages.error(request, 'You are placing bids too quickly. Please wait a moment.')
            return redirect('listing_detail', pk=pk)
        listing = get_object_or_404(AuctionListing, pk=pk)
        now = timezone.now()

        if listing.is_closed or listing.ends_at <= now:
            messages.error(request, 'This auction has already ended.')
            return redirect('listing_detail', pk=pk)

        if not listing.is_active:
            messages.error(request, 'This listing is not currently active.')
            return redirect('listing_detail', pk=pk)

        # Prevent bidding against yourself
        top_bid = listing.bids.order_by('-amount').first()
        if top_bid and top_bid.bidder == request.user:
            messages.warning(request, 'You are already the highest bidder on this item.')
            return redirect('listing_detail', pk=pk)

        form = BidForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Please enter a valid bid amount.')
            return redirect('listing_detail', pk=pk)

        amount = form.cleaned_data['amount']

        if amount < listing.start_price:
            messages.error(
                request,
                f'Your bid must be at least the starting price of ${listing.start_price:.2f}.',
            )
        elif amount <= listing.current_bid:
            messages.error(
                request,
                f'Your bid must be higher than the current bid of ${listing.current_bid:.2f}.',
            )
        else:
            Bid.objects.create(listing=listing, bidder=request.user, amount=amount)
            listing.current_bid = amount
            listing.save(update_fields=['current_bid', 'updated_at'])
            messages.success(
                request,
                f'Your bid of ${amount:.2f} was placed successfully!',
            )

        return redirect('listing_detail', pk=pk)
