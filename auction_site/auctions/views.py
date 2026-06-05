from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DetailView, ListView, UpdateView
from django_ratelimit.decorators import ratelimit

from .forms import BidForm, ProfileUpdateForm
from .models import AuctionCategory, AuctionListing, Bid, Invoice, Seller, UserProfile
from .utils import _safe_send, has_active_subscription


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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save_user(self.request.user)
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['next'] = self.request.GET.get('next') or self.request.POST.get('next', '')
        return ctx

    def get_success_url(self):
        next_url = self.request.POST.get('next', '').strip()
        return next_url if next_url else reverse_lazy('profile')


# ── Auction browsing views ────────────────────────────────────────────────────

class CategoryListingView(ListView):
    template_name = 'auctions/category.html'
    context_object_name = 'listings'

    def get_queryset(self):
        self.category = get_object_or_404(
            AuctionCategory, slug=self.kwargs['slug'], is_active=True
        )
        now = timezone.now()
        return (
            AuctionListing.objects
            .filter(
                category=self.category,
                is_active=True,
                is_closed=False,
                ends_at__gt=now,
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

        # Check if the logged-in user is US-verified
        if self.request.user.is_authenticated:
            profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
            ctx['us_verified'] = profile.country == 'US'
        else:
            ctx['us_verified'] = False

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

        # Membership gate — must hold an active (or admin-exempt) subscription.
        if not has_active_subscription(request.user):
            messages.warning(request, 'A membership is required to place bids.')
            return redirect('subscribe')

        # US-only restriction
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if profile.country != 'US':
            messages.error(
                request,
                'Bidding is restricted to US residents. '
                'Please update your profile with a US country selection.',
            )
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


# ── Buy It Now ─────────────────────────────────────────────────────────────--

class BuyNowView(LoginRequiredMixin, View):

    def post(self, request, pk):
        # Membership gate (buy-now is not subject to the US-only auction rule).
        if not has_active_subscription(request.user):
            messages.warning(request, 'A membership is required to make purchases.')
            return redirect('subscribe')

        with transaction.atomic():
            # Row lock so concurrent purchase attempts serialize — the second
            # one blocks here, then sees is_closed=True below and bails out.
            listing = get_object_or_404(
                AuctionListing.objects.select_for_update().select_related('seller'),
                pk=pk,
            )

            if listing.listing_type != 'buy_now':
                messages.error(request, 'This item is not available for direct purchase.')
                return redirect('listing_detail', pk=pk)

            if listing.is_closed or not listing.is_active or listing.ends_at <= timezone.now():
                messages.error(request, 'Sorry, this item has already been purchased.')
                return redirect('listing_detail', pk=pk)

            if listing.seller is None:
                messages.error(request, 'This item cannot be purchased right now.')
                return redirect('listing_detail', pk=pk)

            listing.is_closed = True
            listing.is_active = False
            listing.winner = request.user
            listing.save(update_fields=['is_closed', 'is_active', 'winner', 'updated_at'])

            invoice = Invoice.objects.create(
                listing=listing,
                buyer=request.user,
                seller=listing.seller,
                amount=listing.buy_now_price,
                shipping_fee=listing.seller.shipping_fee,
                payment_method='',
                is_manually_created=False,
            )

        # Emails are sent after the transaction commits.
        self._send_purchase_emails(listing, request.user, invoice)
        messages.success(
            request,
            'Purchase complete! Your invoice and payment details are below.',
        )
        return redirect('invoice_detail', pk=invoice.pk)

    def _send_purchase_emails(self, listing, buyer, invoice):
        seller = listing.seller
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')

        # Buyer — purchase confirmation with payment instructions.
        if buyer.email:
            body = f"""\
Thank you for your purchase, {buyer.username}!

Item:            {listing.title}
Price:           ${invoice.amount}
Shipping fee:    ${invoice.shipping_fee}
Total:           ${invoice.total}

Seller:          {seller.name}
Accepted payment: {seller.accepted_payment_methods}

Please arrange payment with the seller using one of their accepted methods.
You can view your invoice (#{invoice.pk}) any time from your account.

-- ASQ Daylily Auction Group
"""
            _safe_send(
                subject=f'Your ASQ Daylily purchase: {listing.title}',
                body=body,
                recipients=[buyer.email],
            )

        # Seller — sale notification (falls back to admin if no seller email).
        seller_recipient = (seller.email if seller.email else '') or admin_email
        if seller_recipient:
            body = f"""\
Your item "{listing.title}" was purchased by {buyer.username}.

Price:         ${invoice.amount}
Shipping fee:  ${invoice.shipping_fee}
Buyer email:   {buyer.email or '(not provided)'}
Invoice:       #{invoice.pk}

Please contact the buyer to arrange payment and shipping.

-- ASQ Daylily Auction System
"""
            _safe_send(
                subject=f'Your item sold: {listing.title}',
                body=body,
                recipients=[seller_recipient],
            )

        # Admin — summary.
        if admin_email:
            body = f"""\
[ASQ] Buy It Now — Purchase
===========================
Listing ID:  {listing.pk}
Title:       {listing.title}
Buyer:       {buyer.username} ({buyer.email or 'no email'})
Amount:      ${invoice.amount}
Invoice ID:  {invoice.pk}

-- ASQ Daylily Auction System
"""
            _safe_send(
                subject=f'[ASQ Admin] Buy Now: {listing.title}',
                body=body,
                recipients=[admin_email],
            )
