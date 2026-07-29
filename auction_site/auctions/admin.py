from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
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
        # listing_type and buy_now_price carry over with the cloned instance.
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
    list_filter = ('is_approved', 'subscription_required')
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
        'reserve_price',
        'starts_at',
        'ends_at',
        'is_active',
        'is_closed',
        'winner',
    )
    list_filter = ('listing_type', 'is_active', 'is_closed', 'category', 'seller')
    search_fields = ('title', 'description')
    raw_id_fields = ('winner',)
    date_hierarchy = 'starts_at'
    readonly_fields = ('image_preview',)

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
    fields = ('subscription_required', 'phone_number', 'country', 'address', 'notes')


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
