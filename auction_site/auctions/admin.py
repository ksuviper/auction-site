from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import redirect
from django.urls import reverse
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
    CombinedInvoice,
    FAQItem,
    Invoice,
    ListingComment,
    ProxyBid,
    SitePage,
    Subscription,
    UserProfile,
    Wishlist,
)
from .services import copy_listing

User = get_user_model()

# Derived from the model rather than retyped, so a new payment method appears
# in the admin the moment it is added to UserProfile.PAYMENT_FIELDS.
PAYMENT_FIELD_NAMES = tuple(
    field for field, _label in UserProfile.PAYMENT_FIELDS
) + ('seller_payment_methods',)


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
    """
    Copy the selected listings, then open the copy for editing.

    Copying is delegated to services.copy_listing so this and Copy Existing
    Listing on the Add Listing page cannot disagree about what carries over.

    A copy has no dates — the model requires them — so each one is saved with
    the original's window as a placeholder and left inactive. Landing on the
    change form is the point: dates are the whole reason you are duplicating,
    and the old version dropped you back on the list where it was easy to
    forget.
    """
    copies = []
    for listing in queryset:
        copy = copy_listing(listing)
        copy.starts_at = listing.starts_at
        copy.ends_at = listing.ends_at
        copy.save()
        copies.append(copy)

    if not copies:
        modeladmin.message_user(
            request, 'Nothing selected — nothing copied.', level=messages.INFO
        )
        return

    if len(copies) == 1:
        modeladmin.message_user(
            request,
            f'Copied "{copies[0].title}". Set its dates and tick Is active to '
            'put it live.',
            level=messages.SUCCESS,
        )
        return redirect(
            reverse('admin:auctions_auctionlisting_change', args=[copies[0].pk])
        )

    modeladmin.message_user(
        request,
        f'Copied {len(copies)} listings. Each is inactive and still carries the '
        "original's dates — open them and set new ones before going live.",
        level=messages.SUCCESS,
    )

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
        'user', 'user_email', 'is_approved', 'is_seller', 'phone_number',
        'subscription_required',
    )
    list_editable = ('is_approved', 'is_seller', 'subscription_required')
    list_filter = (ApprovalStatusFilter, 'is_seller', 'subscription_required')
    list_select_related = ('user',)
    search_fields = ('user__username', 'user__email', 'phone_number')
    raw_id_fields = ('user',)
    # Seller details are grouped and labelled so it is obvious they only matter
    # once "Is seller" is ticked — they are inert on a buyer's profile.
    fieldsets = (
        (None, {'fields': ('user', 'is_approved', 'subscription_required')}),
        ('Contact', {'fields': ('phone_number', 'country', 'address', 'notes')}),
        (
            'Seller details',
            {
                'fields': (
                    'is_seller', 'seller_category', 'seller_active_week',
                    'seller_bio', 'seller_shipping_fee',
                    'seller_notify_on_comments',
                ),
                'description': (
                    'Only used when "Is seller" is ticked. Sellers can be '
                    'chosen when adding a listing and get a read-only sales '
                    'dashboard; they cannot create or edit listings themselves.'
                ),
            },
        ),
        (
            'Payment methods',
            {
                'fields': PAYMENT_FIELD_NAMES,
                'description': (
                    'How buyers pay this seller. Free text — a handle, a link, '
                    'an email or a phone number, whatever the seller wants '
                    'shown. Leave a method blank if they do not accept it. '
                    'Buyers see these details on their invoice; the public '
                    'seller page lists only which methods are accepted.'
                ),
            },
        ),
        ('Login & security', {'fields': ('email_login_code_enabled',)}),
    )

    @admin.display(description='Email', ordering='user__email')
    def user_email(self, obj):
        return obj.user.email or '—'


class SellerFilter(admin.SimpleListFilter):
    """
    Filter listings by seller, offering only seller-flagged accounts.

    Django's default FK filter would render an option for every User on the
    site — buyers included — which is both a long list and a misleading one.
    """

    title = 'seller'
    parameter_name = 'seller'

    def lookups(self, request, model_admin):
        sellers = (
            User.objects.filter(profile__is_seller=True)
            .select_related('profile')
            .order_by('username')
        )
        return [(user.pk, user.profile.display_name) for user in sellers]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(seller_id=self.value())
        return queryset


@admin.register(AuctionListing)
class AuctionListingAdmin(ModelAdmin):
    actions = [duplicate_listings]
    list_display = (
        'image_preview',
        'title',
        'category',
        'seller_display',
        'listing_type',
        'start_price',
        'current_bid',
        'buy_now_price',
        'stock_display',
        'shipping_display',
        'reserve_price',
        'starts_at',
        'ends_at',
        'is_active',
        'is_closed',
        'sold_display',
        'winner',
    )
    list_filter = (
        'listing_type', BuyNowStockFilter, 'shipping_mode', 'is_active',
        'is_closed', 'category', SellerFilter,
    )
    search_fields = (
        'title', 'description', 'seller__username', 'seller__email',
        'seller__first_name', 'seller__last_name',
    )
    raw_id_fields = ('winner', 'seller')
    list_select_related = ('seller__profile', 'category')
    date_hierarchy = 'starts_at'
    readonly_fields = ('image_preview',)

    def get_queryset(self, request):
        """
        Annotate whether each row has sold, for the Sold column and the lock.

        An Exists subquery rather than the model property, which would issue one
        query per Buy It Now row on the changelist.
        """
        return super().get_queryset(request).annotate(
            _has_invoice=Exists(
                Invoice.objects.filter(listing=OuterRef('pk'))
            )
        )

    def has_change_permission(self, request, obj=None):
        """
        Freeze a listing once somebody has bought from it.

        Django falls back to the view permission when change is denied, so the
        listing stays fully readable in the admin — which it has to be, since
        invoices point at it — just not editable. A listing that merely expired
        unsold is untouched by this and can be re-dated and run again.

        obj is None on the changelist and for the actions; denying there would
        take the whole model read-only, including Duplicate.
        """
        if obj is not None and obj.has_completed_sale:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """
        Deleting a sold listing is refused for the same reason as editing it.

        Invoice.listing is PROTECT, so the delete would fail at the database
        anyway — this turns an unexplained error page into a greyed-out button.
        """
        if obj is not None and obj.has_completed_sale:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description='Sold', boolean=True, ordering='_has_invoice')
    def sold_display(self, obj):
        """Whether this listing is locked — visible without opening each one."""
        return obj.has_completed_sale

    @admin.display(description='Seller', ordering='seller__username')
    def seller_display(self, obj):
        """The seller's display name — a raw User row shows only a username."""
        profile = getattr(obj.seller, 'profile', None)
        if profile is None:
            return obj.seller.get_username()
        return profile.display_name

    @admin.display(description='Shipping', ordering='shipping_fee')
    def shipping_display(self, obj):
        """Effective rate and how it is charged; '(seller)' when inherited."""
        if obj.listing_type != 'buy_now':
            return '—'
        source = '' if obj.shipping_fee is not None else ' (seller)'
        per = ' each' if obj.shipping_mode == 'per_item' else ' flat'
        return f'${obj.shipping_rate}{per}{source}'

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
    search_fields = (
        'listing__title', 'item_description', 'buyer__username',
        'seller__username', 'seller__email',
    )
    raw_id_fields = ('listing', 'buyer', 'seller')
    date_hierarchy = 'created_at'


# ── Combined invoices ────────────────────────────────────────────────────────

def draft_invoice_count(request):
    """
    Sidebar badge: how many drafts are waiting to be reviewed and sent.

    Worth a badge because nothing reaches a buyer on its own any more — a draft
    left unsent is a buyer who has heard nothing about plants they won. Always
    a string, including '0': unfold prints the configured dotted path when a
    badge callback returns something falsy.
    """
    return str(CombinedInvoice.objects.filter(status='draft').count())


class InvoiceLineInline(TabularInline):
    """
    The line items on a combined invoice, read-only.

    Billing adjustments happen on the review page; a line's amount is what the
    buyer committed to when they bid or bought, so it is not something to nudge
    from inside the bill.
    """

    model = Invoice
    fk_name = 'combined_invoice'
    extra = 0
    can_delete = False
    fields = ('item_display', 'quantity', 'amount', 'shipping_fee', 'created_at')
    readonly_fields = fields
    verbose_name = 'Line item'
    verbose_name_plural = 'Line items'

    def has_add_permission(self, request, obj):
        """Lines arrive by being generated, never by being typed in here."""
        return False

    @admin.display(description='Item')
    def item_display(self, obj):
        return obj.item_display


@admin.register(CombinedInvoice)
class CombinedInvoiceAdmin(ModelAdmin):
    """
    Record-keeping view. The working UI is at /admin/combined-invoices/.

    Generating, adjusting and sending all happen there — this is for looking
    things up, and for the status filter the client asked for.
    """

    list_display = (
        'pk', 'buyer', 'seller_display', 'status', 'item_count',
        'subtotal_display', 'shipping_display', 'discount_amount',
        'total_display', 'created_at', 'sent_at', 'review_link',
    )
    list_filter = ('status',)
    search_fields = (
        'buyer__username', 'buyer__email', 'seller__username', 'seller__email',
    )
    raw_id_fields = ('buyer', 'seller')
    readonly_fields = ('created_at', 'sent_at')
    date_hierarchy = 'created_at'
    inlines = [InvoiceLineInline]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('buyer', 'seller__profile')
            .prefetch_related('line_items')
            .annotate(_item_count=Count('line_items'))
        )

    def has_change_permission(self, request, obj=None):
        """
        A sent invoice is a record of what a buyer was told they owe.

        Editing its shipping or discount afterwards would leave the site
        disagreeing with the email in their inbox.
        """
        if obj is not None and obj.status == 'sent':
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """
        Deleting a sent invoice would silently un-bill its line items.

        Invoice.combined_invoice is SET_NULL, so the items would return to the
        unbilled pool and the next Generate run would bill the buyer a second
        time for plants they have already been asked to pay for. Deleting a
        *draft* is fine and does exactly the useful thing: the items go back to
        the pool to be regrouped.
        """
        if obj is not None and obj.status == 'sent':
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description='Seller', ordering='seller__username')
    def seller_display(self, obj):
        profile = getattr(obj.seller, 'profile', None)
        return profile.display_name if profile else obj.seller.get_username()

    @admin.display(description='Items', ordering='_item_count')
    def item_count(self, obj):
        return obj._item_count

    @admin.display(description='Subtotal')
    def subtotal_display(self, obj):
        return f'${obj.subtotal}'

    @admin.display(description='Shipping')
    def shipping_display(self, obj):
        suffix = ' (set)' if obj.shipping_override is not None else ''
        return f'${obj.total_shipping}{suffix}'

    @admin.display(description='Total')
    def total_display(self, obj):
        return f'${obj.total}'

    @admin.display(description='Review')
    def review_link(self, obj):
        url = reverse('combined_invoice_review', args=[obj.pk])
        label = 'Review &amp; send' if obj.status == 'draft' else 'View'
        return mark_safe(f'<a href="{url}">{label}</a>')


# ── Admin-managed content pages ──────────────────────────────────────────────

@admin.register(FAQItem)
class FAQItemAdmin(ModelAdmin):
    list_display = ('question', 'display_order', 'is_published')
    list_editable = ('display_order', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('question', 'answer')
    # Model Meta already orders by display_order; repeated here because
    # list_editable on an ordering field is confusing without it being visible.
    ordering = ('display_order', 'question')


@admin.register(SitePage)
class SitePageAdmin(ModelAdmin):
    list_display = ('title', 'slug', 'updated_at', 'view_on_site_link')
    search_fields = ('title', 'slug', 'body')
    readonly_fields = ('updated_at',)
    prepopulated_fields = {'slug': ('title',)}

    @admin.display(description='Public page')
    def view_on_site_link(self, obj):
        """A direct link, since the address depends on the slug."""
        url = obj.get_absolute_url()
        return mark_safe(f'<a href="{url}" target="_blank">{url}</a>')


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
        'is_approved', 'is_seller', 'subscription_required', 'phone_number',
        'country', 'address', 'notes', 'seller_category', 'seller_active_week',
        'seller_bio', 'seller_shipping_fee', 'seller_notify_on_comments',
    ) + PAYMENT_FIELD_NAMES


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
    list_display = BaseUserAdmin.list_display + ('approval_status', 'is_seller')
    list_filter = BaseUserAdmin.list_filter + (
        'profile__is_approved', 'profile__is_seller',
    )
    list_select_related = ('profile',)

    @admin.display(description='Approval', ordering='profile__is_approved')
    def approval_status(self, obj):
        if obj.is_staff or obj.is_superuser:
            return 'Exempt (staff)'
        profile = getattr(obj, 'profile', None)
        if profile is None:
            return 'No profile'
        return 'Approved' if profile.is_approved else 'Awaiting approval'

    @admin.display(
        description='Seller', boolean=True, ordering='profile__is_seller'
    )
    def is_seller(self, obj):
        """Whether this account can be picked when adding a listing."""
        return bool(getattr(getattr(obj, 'profile', None), 'is_seller', False))
