from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView, UpdateView

from allauth.account.views import SignupView as AllauthSignupView
from django_ratelimit.decorators import ratelimit

from .forms import (
    BidForm,
    BuyNowForm,
    CommentForm,
    ProfileUpdateForm,
    ProxyBidForm,
)
from .models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    Invoice,
    ListingComment,
    ProxyBid,
    UserProfile,
)
from .services import run_proxy_bids
from .utils import _safe_send, has_active_subscription, payment_block

User = get_user_model()


# ── Registration ─────────────────────────────────────────────────────────────

@method_decorator(
    # block=True (unlike the bid views' block=False) so signup abuse hard-stops
    # with a 403 instead of being flagged and allowed through.
    ratelimit(key='ip', rate='5/h', method='POST', block=True),
    name='post',
)
class RateLimitedSignupView(AllauthSignupView):
    """
    allauth's signup view plus per-IP throttling and Turnstile verification.

    Registered ahead of include('allauth.urls') in the project URLconf so it
    owns /accounts/signup/ — see auction_site/urls.py.
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # CustomSignupForm only runs the Turnstile check when asked to, which
        # keeps social signup (same form base class) free of it.
        kwargs['request'] = self.request
        kwargs['require_turnstile'] = True
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Scoped to this page rather than a global context processor: the widget
        # only ever renders on the email/password signup form.
        context['turnstile_site_key'] = settings.TURNSTILE_SITE_KEY
        return context


class SecuritySettingsView(LoginRequiredMixin, TemplateView):
    """
    "Login & Security": the email-code preference plus a way into allauth's MFA.

    Read-only summary and links; every state change happens either in
    ToggleEmailLoginCodeView below or in allauth's own MFA views, which are
    linked to rather than reimplemented.
    """

    template_name = 'auctions/security_settings.html'

    def get_context_data(self, **kwargs):
        from allauth.mfa.models import Authenticator

        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)

        authenticators = set(
            Authenticator.objects.filter(user=user).values_list('type', flat=True)
        )

        context['profile'] = profile
        context['email_code_locked'] = user.is_staff or user.is_superuser
        context['totp_active'] = Authenticator.Type.TOTP in authenticators
        context['recovery_codes_active'] = (
            Authenticator.Type.RECOVERY_CODES in authenticators
        )
        return context


class ToggleEmailLoginCodeView(LoginRequiredMixin, View):
    """
    Flip the user's email-code preference.

    POST only, and it re-checks the staff rule server-side. The settings page
    hides the control for staff, but hiding a control is not enforcement — the
    adapter would ignore the flag for staff anyway, so letting it be written
    would just leave a misleading value in the database.
    """

    def post(self, request, *args, **kwargs):
        if request.user.is_staff or request.user.is_superuser:
            return HttpResponseForbidden(
                'Staff accounts require an emailed login code and cannot '
                'disable it.'
            )

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.email_login_code_enabled = not profile.email_login_code_enabled
        profile.save(update_fields=['email_login_code_enabled'])

        if profile.email_login_code_enabled:
            messages.success(
                request,
                'Login codes are back on. We\'ll email you a code each time '
                'you sign in.',
            )
        else:
            messages.info(
                request,
                'Login codes are off. You\'ll sign in with just your password '
                'from now on.',
            )
        return redirect('security_settings')


class AccountPendingApprovalView(TemplateView):
    """
    Explains that an account is verified but still waiting on an admin.

    Deliberately open to anonymous visitors: the whole point is that the user
    cannot hold a session yet, so requiring a login would make the page
    unreachable exactly when it is needed.
    """

    template_name = 'account/pending_approval.html'


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
            .select_related('seller__profile', 'category')
            .order_by('ends_at')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['category'] = self.category
        return ctx


class SellerListingView(ListView):
    """A seller's public page: who they are, and what they have open now."""

    template_name = 'auctions/seller_listings.html'
    context_object_name = 'listings'

    def get_queryset(self):
        # Filtering on profile__is_seller matters: without it this URL would
        # render a public page for any user account whose pk was guessed.
        self.seller = get_object_or_404(
            User.objects.select_related('profile'),
            pk=self.kwargs['pk'],
            profile__is_seller=True,
        )
        return self.seller.profile.active_listings()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['seller'] = self.seller
        ctx['seller_profile'] = self.seller.profile
        return ctx


class SellerDashboardView(LoginRequiredMixin, TemplateView):
    """
    A seller's own read-only record of what is listed and what has sold.

    Visibility only. Sellers do not create or edit listings — an admin does,
    through Add Listing — so there is deliberately nothing actionable here; a
    correction is a conversation with an admin, not a form on this page.
    """

    template_name = 'auctions/seller_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if profile is None or not profile.is_seller:
                messages.info(
                    request,
                    'The seller dashboard is only available to seller accounts.',
                )
                return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # Every query below is filtered to this user. A seller seeing another
        # seller's sales is the one thing this page must never do.
        active = list(profile.active_listings())
        sales = (
            Invoice.objects
            .filter(seller=self.request.user)
            .select_related('listing', 'buyer')
            .order_by('-created_at')
        )

        totals = sales.aggregate(
            units=Coalesce(Sum('quantity'), 0),
            revenue=Coalesce(Sum('amount'), Decimal('0.00')),
        )

        ctx['profile'] = profile
        ctx['active_listings'] = active
        ctx['active_count'] = len(active)
        ctx['sales'] = sales
        ctx['units_sold'] = totals['units']
        ctx['total_revenue'] = totals['revenue']
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
        if listing.listing_type == 'buy_now':
            ctx['buy_now_form'] = BuyNowForm(listing=listing)
        ctx['proxy_bid_form'] = ProxyBidForm()
        ctx['comment_form'] = CommentForm()
        ctx['comments'] = (
            listing.comments.filter(is_approved=True, parent=None)
            .select_related('author')
            .prefetch_related('replies__author')
            .order_by('created_at')
        )

        # The logged-in user's active proxy bid on this listing, if any.
        ctx['proxy_bid'] = None
        if self.request.user.is_authenticated:
            profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
            ctx['us_verified'] = profile.country == 'US'
            ctx['proxy_bid'] = listing.proxy_bids.filter(
                bidder=self.request.user, is_active=True
            ).first()
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
            with transaction.atomic():
                Bid.objects.create(listing=listing, bidder=request.user, amount=amount)
                listing.current_bid = amount
                listing.save(update_fields=['current_bid', 'updated_at'])
                proxy_winner = run_proxy_bids(listing)

            messages.success(
                request,
                f'Your bid of ${amount:.2f} was placed successfully!',
            )
            if proxy_winner is not None and proxy_winner != request.user:
                listing.refresh_from_db(fields=['current_bid'])
                messages.info(
                    request,
                    f'You were immediately outbid by a proxy bidder. '
                    f'Current bid: ${listing.current_bid:.2f}.',
                )

        return redirect('listing_detail', pk=pk)


# ── Buy It Now ─────────────────────────────────────────────────────────────--

class BuyNowView(LoginRequiredMixin, View):

    def post(self, request, pk):
        # Membership gate.
        if not has_active_subscription(request.user):
            messages.warning(request, 'A membership is required to make purchases.')
            return redirect('subscribe')

        # US-only restriction (same as bidding).
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if profile.country != 'US':
            messages.error(
                request,
                'Purchases are restricted to US residents. '
                'Please update your profile with a US country selection.',
            )
            return redirect('listing_detail', pk=pk)

        with transaction.atomic():
            # Row lock so concurrent purchase attempts serialize — the second
            # one blocks here, then re-reads the stock count below.
            listing = get_object_or_404(
                AuctionListing.objects.select_for_update()
                .select_related('seller__profile'),
                pk=pk,
            )

            if listing.listing_type != 'buy_now':
                messages.error(request, 'This item is not available for direct purchase.')
                return redirect('listing_detail', pk=pk)

            if listing.is_closed or not listing.is_active or listing.ends_at <= timezone.now():
                messages.error(request, 'Sorry, this item is no longer available.')
                return redirect('listing_detail', pk=pk)

            # Bind the form to the locked row. Its check is against stock nobody
            # else can be changing right now, so passing it here is decisive —
            # unlike a check made before the lock was held.
            form = BuyNowForm(request.POST, listing=listing)
            if not form.is_valid():
                remaining = listing.units_remaining
                if remaining <= 0:
                    messages.error(
                        request, 'Sorry, this item has just sold out.'
                    )
                else:
                    messages.error(
                        request,
                        f'Sorry, only {remaining} remaining — please adjust '
                        'your quantity.',
                    )
                return redirect('listing_detail', pk=pk)

            quantity = form.cleaned_data['quantity']

            # The decisive guard. One UPDATE that both tests and decrements the
            # stock, so the check cannot be separated from the write by another
            # request. This does not rely on the row lock above: SQLite reports
            # has_select_for_update = False, which makes select_for_update() a
            # silent no-op there — and this project defaults to SQLite. A
            # conditional UPDATE is atomic on every backend.
            claimed = AuctionListing.objects.filter(
                pk=listing.pk, quantity_remaining__gte=quantity
            ).update(
                quantity_remaining=F('quantity_remaining') - quantity,
                updated_at=timezone.now(),
            )

            if not claimed:
                # Another buyer took the stock in between. Nothing was written,
                # so there is no partial purchase to unwind.
                listing.refresh_from_db(fields=['quantity_remaining'])
                remaining = listing.units_remaining
                if remaining <= 0:
                    messages.error(request, 'Sorry, this item has just sold out.')
                else:
                    messages.error(
                        request,
                        f'Sorry, only {remaining} remaining — please adjust '
                        'your quantity.',
                    )
                return redirect('listing_detail', pk=pk)

            listing.refresh_from_db(fields=['quantity_remaining'])

            # Stock caps availability; it is not what ends a listing. One with
            # units left stays open until ends_at, and close_ended_auctions
            # closes it then regardless of leftover stock.
            #
            # `winner` stays untouched: it means "the single person who won this
            # lot", which no longer describes a listing several buyers can each
            # take a share of. Who bought what lives in the Invoice rows.
            if listing.quantity_remaining == 0:
                listing.is_closed = True
                listing.is_active = False
                listing.save(update_fields=['is_closed', 'is_active', 'updated_at'])

            invoice = Invoice.objects.create(
                listing=listing,
                buyer=request.user,
                seller=listing.seller,
                quantity=quantity,
                amount=listing.buy_now_price * quantity,
                shipping_fee=listing.shipping_for(quantity),
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
        seller_profile = getattr(seller, 'profile', None)
        seller_name = (
            seller_profile.display_name if seller_profile
            else seller.get_username()
        )
        payment_methods = payment_block(seller_profile)
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')
        shipping_note = (
            'per item' if listing.shipping_mode == 'per_item' else 'flat rate'
        )

        # Buyer — purchase confirmation with payment instructions.
        if buyer.email:
            body = f"""\
Thank you for your purchase, {buyer.username}!

Item:            {listing.title}
Quantity:        {invoice.quantity}
Price each:      ${listing.buy_now_price}
Item total:      ${invoice.amount}
Shipping fee:    ${invoice.shipping_fee} ({shipping_note})
Total:           ${invoice.total}

Seller:          {seller_name}
Accepted payment: {payment_methods}

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
        seller_recipient = seller.email or admin_email
        if seller_recipient:
            body = f"""\
Your item "{listing.title}" was purchased by {buyer.username}.

Quantity:      {invoice.quantity} of {listing.quantity_available}
Price each:    ${listing.buy_now_price}
Item total:    ${invoice.amount}
Shipping fee:  ${invoice.shipping_fee} ({shipping_note})
Still in stock: {listing.units_remaining}
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
Quantity:    {invoice.quantity} @ ${listing.buy_now_price} each
Amount:      ${invoice.amount}
Shipping:    ${invoice.shipping_fee} ({shipping_note})
Remaining:   {listing.units_remaining} of {listing.quantity_available}
Invoice ID:  {invoice.pk}

-- ASQ Daylily Auction System
"""
            _safe_send(
                subject=f'[ASQ Admin] Buy Now: {listing.title}',
                body=body,
                recipients=[admin_email],
            )


# ── Proxy (automatic) bidding ──────────────────────────────────────────────--

@method_decorator(
    ratelimit(key='user', rate='10/m', method='POST', block=False),
    name='post',
)
class PlaceProxyBidView(LoginRequiredMixin, View):

    def post(self, request, pk):
        if getattr(request, 'limited', False):
            messages.error(request, 'You are bidding too quickly. Please wait a moment.')
            return redirect('listing_detail', pk=pk)

        # Membership gate.
        if not has_active_subscription(request.user):
            messages.warning(request, 'A membership is required to place bids.')
            return redirect('subscribe')

        # US-only restriction (same as PlaceBidView).
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

        if listing.listing_type != 'auction':
            messages.error(request, 'Automatic bidding is only available on auction listings.')
            return redirect('listing_detail', pk=pk)

        if listing.is_closed or not listing.is_active or listing.ends_at <= now:
            messages.error(request, 'This auction is not currently accepting bids.')
            return redirect('listing_detail', pk=pk)

        form = ProxyBidForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Please enter a valid maximum bid amount.')
            return redirect('listing_detail', pk=pk)

        max_amount = form.cleaned_data['max_amount']
        if max_amount <= listing.current_bid:
            messages.error(
                request,
                f'Your maximum bid must be higher than the current bid of '
                f'${listing.current_bid:.2f}.',
            )
            return redirect('listing_detail', pk=pk)

        # update_or_create lets a bidder raise their existing maximum.
        ProxyBid.objects.update_or_create(
            listing=listing,
            bidder=request.user,
            defaults={'max_amount': max_amount, 'is_active': True},
        )
        run_proxy_bids(listing)
        messages.success(
            request,
            f'Your maximum bid of ${max_amount:.2f} is set. '
            f'We will bid for you automatically up to that amount.',
        )
        return redirect('listing_detail', pk=pk)


# ── Listing comments & questions ───────────────────────────────────────────--

class PostCommentView(LoginRequiredMixin, View):
    """Post a question/comment on a listing. Login required, no subscription."""

    def post(self, request, pk):
        listing = get_object_or_404(AuctionListing, pk=pk)
        form = CommentForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Please enter a question or comment.')
            return redirect('listing_detail', pk=pk)

        comment = ListingComment.objects.create(
            listing=listing,
            author=request.user,
            body=form.cleaned_data['body'],
            is_approved=False,
        )
        self._notify_new_comment(request, listing, comment)
        messages.success(
            request,
            'Your question has been submitted and will appear after review.',
        )
        return redirect('listing_detail', pk=pk)

    def _notify_new_comment(self, request, listing, comment):
        recipients = []
        admin_email = getattr(settings, 'ADMIN_EMAIL', '')
        if admin_email:
            recipients.append(admin_email)
        seller_profile = getattr(listing.seller, 'profile', None)
        if (
            listing.seller.email
            and getattr(seller_profile, 'seller_notify_on_comments', False)
        ):
            recipients.append(listing.seller.email)
        if not recipients:
            return

        admin_link = request.build_absolute_uri(
            reverse('admin:auctions_listingcomment_changelist')
        )
        body = f"""\
A new question was posted on: {listing.title}

From:     {comment.author.username}
Question: {comment.body}

Review and approve it here:
{admin_link}

-- ASQ Daylily Auction System
"""
        _safe_send(
            subject=f'New question on: {listing.title}',
            body=body,
            recipients=recipients,
        )
