from django.contrib import admin
from django.utils.html import mark_safe
from unfold.admin import ModelAdmin

from .models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    Invoice,
    Seller,
    UserProfile,
    Wishlist,
)


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
    prepopulated_fields = {'slug': ('name',)}
    exclude = ('slug',)


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ('user', 'phone_number')
    search_fields = ('user__username', 'user__email', 'phone_number')
    raw_id_fields = ('user',)


@admin.register(Seller)
class SellerAdmin(ModelAdmin):
    list_display = ('name', 'email', 'active_week', 'shipping_fee', 'accepted_payment_methods')
    list_filter = ('active_week',)
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
        'start_price',
        'current_bid',
        'reserve_price',
        'starts_at',
        'ends_at',
        'is_active',
        'is_closed',
        'winner',
    )
    list_filter = ('is_active', 'is_closed', 'category', 'seller')
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
