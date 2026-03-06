from django.contrib import admin

from .models import AuctionCategory, AuctionListing

admin.site.site_header = 'Auction Admin'
admin.site.site_title = 'Auction Admin Portal'
admin.site.index_title = 'Management Dashboard'


@admin.register(AuctionCategory)
class AuctionCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(AuctionListing)
class AuctionListingAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'category',
        'start_price',
        'current_bid',
        'starts_at',
        'ends_at',
        'is_active',
    )
    list_filter = ('is_active', 'category')
    search_fields = ('title', 'description')
    autocomplete_fields = ('category',)
    date_hierarchy = 'starts_at'
