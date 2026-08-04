from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q
from django.utils.html import mark_safe
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from .models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    Invoice,
    ListingComment,
    ProxyBid,
    Seller,
    Subscription,
    UserProfile,
    Wishlist,
)

User = get_user_model()


class BuyNowStockFilter(admin.SimpleListFilter):
    """Filter Buy It Now listings by whether any units are left.

    A plain filter on quantity_remaining would offer one option per count, and
    would lump every auction listing under "None" — auction listings do not use
    stock at all.
    """

    title = 'Buy It Now stock'
    parameter_name = 'stock'

    def lookups(self, request, model_admin):
        return [
            ('in_stock', 'In stock'),
            ('sold_out', 'Sold out'),
            ('not_applicable', 'Auction (no stock)'),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'in_stock':
            return queryset.filter(
                listing_type='buy_now', quantity_remaining__gt=0
            )
        if value == 'sold_out':
            return queryset.filter(listing_type='buy_now', quantity_remaining=0)
        if value == 'not_applicable':
            return queryset.exclude(listing_type='buy_now')
        return queryset


@admin.action(description='Duplicate selected listing(s) for re-use next week')
def duplicate_listings(modeladmin, request, queryset):
    count = queryset.count()
    for listing in queryset:
        listing.pk = None
        listing._state.adding = True
        listing.winner = None
        listing.is_closed = False
        listing.is_active = True
        listing.current_bid = 0
        # listing_type, buy_now_price, quantity_available and shipping_mode all
        # carry over with the cloned instance. Stock is reset rather than
        # inherited: a duplicate is next week's listing, so it starts fully
        # available instead of picking up how far the original sold down.
        if listing.listing_type == 'buy_now':
            listing.quantity_remaining = listing.quantity_available
        listing.save()
    modeladmin.message_user(request, f'Duplicated {count} listing(s). Update dates before going live.')

admin.site.site_header = 'ASQ Daylily Auctions Admin'
admin.site.site_title = 'ASQ Daylily Auctions Admin Portal'
admin.site.index_title = 'Management Dashboard'


@admin.register(AuctionCategory)
class AuctionCategoryAdmin(ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
    exclude = ('slug',)


def pending_approval(queryset):
    """
    Narrow ``queryset`` (of UserProfile) to accounts actually waiting on an admin.

    Staff and superusers are excluded because the gate does not apply to them —
    a superuser's own profile starts out is_approved=False and would otherwise
    sit in the review queue forever. Shared by the changelist filter and the
    sidebar badge so the two can never disagree.
    """
    return (
        queryset.filter(is_approved=False)
        .exclude(user__is_staff=True)
        .exclude(user__is_superuser=True)
    )


def pending_approval_count(request):
    """
    Sidebar badge for the pending-approval queue (wired up in UNFOLD settings).

    Always returns a string, including '0': unfold renders the badge whenever one
    is configured, and falls back to printing the configured dotted path if the
    callback is falsy.
    """
    return str(pending_approval(UserProfile.objects.all()).count())


class ApprovalStatusFilter(admin.SimpleListFilter):
    """
    Approval filter that keeps staff out of the pending bucket.

    A plain ``is_approved`` boolean filter would list every staff account under
    "No", since nothing ever approves them.
    """

    title = 'approval status'
    parameter_name = 'approval'

    def lookups(self, request, model_admin):
        return [
            ('pending', 'Awaiting approval'),
            ('approved', 'Approved'),
            ('exempt', 'Staff — gate does not apply'),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'pending':
            return pending_approval(queryset)
        if value == 'approved':
            return queryset.filter(is_approved=True)
        if value == 'exempt':
            return queryset.filter(
                Q(user__is_staff=True) | Q(user__is_superuser=True)
            )
        return queryset


@admin.action(description='Approve selected account(s) and notify the user')
def approve_selected_users(modeladmin, request, queryset):
    """
    Approve the selected accounts.

    Saves each row one at a time rather than issuing a bulk update, because the
    "you've been approved" email is sent by the post_save receiver in signals.py
    — that way approving inline from the list view sends it too.
    """
    pending = list(queryset.filter(is_approved=False).select_related('user'))
    if not pending:
        modeladmin.message_user(
            request,
            'Nothing to do — every selected account was already approved.',
            level=messages.INFO,
        )
        return

    for profile in pending:
        profile.is_approved = True
        profile.save(update_fields=['is_approved'])

    modeladmin.message_user(
        request,
        f'Approved {len(pending)} account(s); each has been emailed a sign-in link.',
        level=messages.SUCCESS,
    )


@admin.action(description='Revoke approval for selected account(s)')
def revoke_approval(modeladmin, request, queryset):
    """
    Undo a mistaken approval, or suspend an account.

    Deliberately silent — the user is not emailed. Revocation is either an admin
    correcting themselves or a moderation decision, and neither is improved by
    an automated "your access was removed" message.
    """
    revoked = queryset.filter(is_approved=True).update(is_approved=False)
    modeladmin.message_user(
        request,
        f'Revoked approval for {revoked} account(s). They can no longer log in.',
        level=messages.WARNING if revoked else messages.INFO,
    )


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    actions = [approve_selected_users, revoke_approval]
    # user_email is shown because the username allauth derives at signup (e.g.
    # 'ada') is not something an admin can recognise a person by — the address
    # they registered with is.
    list_display = (
        'user', 'user_email', 'is_approved', 'phone_number', 'subscription_required',
    )
    list_editable = ('is_approved', 'subscription_required')
    list_filter = (ApprovalStatusFilter, 'subscription_required')
    list_select_related = ('user',)
    search_fields = ('user__username', 'user__email', 'phone_number')
    raw_id_fields = ('user',)

    @admin.display(description='Email', ordering='user__email')
    def user_email(self, obj):
        return obj.user.email or '—'


@admin.register(Seller)
class SellerAdmin(ModelAdmin):
    list_display = (
        'name', 'email', 'notify_on_comments', 'active_week',
        'shipping_fee', 'accepted_payment_methods',
    )
    list_editable = ('notify_on_comments',)
    list_filter = ('active_week', 'notify_on_comments')
    search_fields = ('name', 'email', 'bio')
    date_hierarchy = 'active_week'


@admin.register(AuctionListing)
class AuctionListingAdmin(ModelAdmin):
    actions = [duplicate_listings]
    list_display = (
        'image_preview',
        'title',
        'category',
        'seller',
        'listing_type',
        'start_price',
        'current_bid',
        'buy_now_price',
        'stock_display',
        'shipping_mode',
        'reserve_price',
        'starts_at',
        'ends_at',
        'is_active',
        'is_closed',
        'winner',
    )
    list_filter = (
        'listing_type', BuyNowStockFilter, 'shipping_mode', 'is_active',
        'is_closed', 'category', 'seller',
    )
    search_fields = ('title', 'description')
    raw_id_fields = ('winner',)
    date_hierarchy = 'starts_at'
    readonly_fields = ('image_preview',)

    @admin.display(description='Stock', ordering='quantity_remaining')
    def stock_display(self, obj):
        """Remaining / total, or a dash for auction listings."""
        if obj.listing_type != 'buy_now':
            return '—'
        if obj.units_remaining == 0:
            return 'Sold out'
        return f'{obj.units_remaining} of {obj.quantity_available}'

    @admin.display(description='Preview')
    def image_preview(self, obj):
        if obj.image:
            return mark_safe(
                f'<img src="{obj.image.url}" style="height:60px;width:auto;border-radius:4px;" />'
            )
        return '—'


@admin.register(Bid)
class BidAdmin(ModelAdmin):
    list_display = ('listing', 'bidder', 'amount', 'placed_at')
    list_filter = ('placed_at',)
    search_fields = ('listing__title', 'bidder__username')
    raw_id_fields = ('listing', 'bidder')
    date_hierarchy = 'placed_at'


@admin.register(ProxyBid)
class ProxyBidAdmin(ModelAdmin):
    list_display = ('listing', 'bidder', 'max_amount', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('listing__title', 'bidder__username')
    raw_id_fields = ('listing', 'bidder')


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = (
        'pk',
        'item_display',
        'buyer',
        'seller',
        'quantity',
        'amount',
        'shipping_fee',
        'payment_method',
        'is_sent',
        'is_manually_created',
        'created_at',
    )
    list_filter = ('is_sent', 'is_manually_created', 'payment_method')
    search_fields = ('listing__title', 'item_description', 'buyer__username', 'seller__name')
    raw_id_fields = ('listing', 'buyer', 'seller')
    date_hierarchy = 'created_at'


@admin.register(Wishlist)
class WishlistAdmin(ModelAdmin):
    list_display = ('user', 'listing_title_keyword', 'notified')
    list_filter = ('notified',)
    search_fields = ('user__username', 'listing_title_keyword')
    raw_id_fields = ('user',)


# ── Listing comments & questions ─────────────────────────────────────────────

@admin.action(description='Approve selected comments')
def approve_comments(modeladmin, request, queryset):
    updated = queryset.update(is_approved=True)
    modeladmin.message_user(request, f'Approved {updated} comment(s).')


@admin.action(description='Reject (unapprove) selected comments')
def reject_comments(modeladmin, request, queryset):
    updated = queryset.update(is_approved=False)
    modeladmin.message_user(request, f'Rejected {updated} comment(s).')


class ReplyInline(TabularInline):
    model = ListingComment
    fk_name = 'parent'
    fields = ('author', 'body', 'is_approved')
    raw_id_fields = ('author',)
    extra = 0
    verbose_name = 'Reply'
    verbose_name_plural = 'Replies'


@admin.register(ListingComment)
class ListingCommentAdmin(ModelAdmin):
    list_display = ('listing', 'author', 'short_body', 'is_approved', 'created_at')
    list_filter = ('is_approved', 'created_at')
    search_fields = ('listing__title', 'author__username', 'body')
    raw_id_fields = ('listing', 'author', 'parent')
    actions = [approve_comments, reject_comments]
    inlines = [ReplyInline]

    @admin.display(description='Comment')
    def short_body(self, obj):
        return obj.body if len(obj.body) <= 60 else f'{obj.body[:60]}…'


# ── Subscriptions ───────────────────────────────────────────────────────────

@admin.action(description='Mark selected subscriptions Active')
def mark_active(modeladmin, request, queryset):
    updated = queryset.update(status='active')
    modeladmin.message_user(request, f'{updated} subscription(s) marked active.')


@admin.action(description='Mark selected subscriptions Lapsed')
def mark_lapsed(modeladmin, request, queryset):
    updated = queryset.update(status='lapsed')
    modeladmin.message_user(request, f'{updated} subscription(s) marked lapsed.')


@admin.action(description='Exempt user from subscription requirement')
def exempt_from_subscription(modeladmin, request, queryset):
    count = 0
    for sub in queryset.select_related('user__profile'):
        profile = getattr(sub.user, 'profile', None)
        if profile is not None:
            profile.subscription_required = False
            profile.save(update_fields=['subscription_required'])
            count += 1
    modeladmin.message_user(
        request, f'Exempted {count} user(s) from the subscription requirement.'
    )


@admin.action(description='Require subscription for selected users')
def require_subscription(modeladmin, request, queryset):
    count = 0
    for sub in queryset.select_related('user__profile'):
        profile = getattr(sub.user, 'profile', None)
        if profile is not None:
            profile.subscription_required = True
            profile.save(update_fields=['subscription_required'])
            count += 1
    modeladmin.message_user(
        request, f'Required a subscription for {count} user(s).'
    )


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = (
        'user',
        'plan',
        'status',
        'current_period_end',
        'grace_period_end',
        'subscription_required_display',
        'paypal_subscription_id',
    )
    list_filter = ('status', 'plan')
    search_fields = ('user__username', 'user__email', 'paypal_subscription_id')
    raw_id_fields = ('user',)
    actions = [mark_active, mark_lapsed, exempt_from_subscription, require_subscription]

    @admin.display(description='Sub. required')
    def subscription_required_display(self, obj):
        profile = getattr(obj.user, 'profile', None)
        if profile is None:
            return '—'
        return 'Yes' if profile.subscription_required else 'No'


# ── User admin with profile inline ───────────────────────────────────────────

class UserProfileInline(StackedInline):
    model = UserProfile
    can_delete = False
    max_num = 1
    extra = 0
    verbose_name_plural = 'Profile'
    # is_approved first: whether this person can log in at all is the most
    # consequential thing on the page. Ticking it here emails them, same as the
    # changelist action — see notify_user_of_approval in signals.py.
    fields = (
        'is_approved', 'subscription_required', 'phone_number', 'country',
        'address', 'notes',
    )


# Replace the default auth User admin so the profile (and its
# subscription_required flag) is editable inline. UserAdmin only renders
# inlines on the change page, so the auto-created profile already exists.
if admin.site.is_registered(User):
    admin.site.unregister(User)


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = [UserProfileInline]
    # Approval decides whether an account can log in, so it belongs on the list
    # people actually browse — not only on the User profiles changelist.
    list_display = BaseUserAdmin.list_display + ('approval_status',)
    list_filter = BaseUserAdmin.list_filter + ('profile__is_approved',)
    list_select_related = ('profile',)

    @admin.display(description='Approval', ordering='profile__is_approved')
    def approval_status(self, obj):
        if obj.is_staff or obj.is_superuser:
            return 'Exempt (staff)'
        profile = getattr(obj, 'profile', None)
        if profile is None:
            return 'No profile'
        return 'Approved' if profile.is_approved else 'Awaiting approval'
