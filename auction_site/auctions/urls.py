from django.urls import path

from .invoice_views import InvoiceDetailView
from .views import (
    CategoryListingView,
    ListingDetailView,
    PlaceBidView,
    ProfileDetailView,
    ProfileUpdateView,
    SellerListingView,
)

urlpatterns = [
    path('profile/', ProfileDetailView.as_view(), name='profile'),
    path('profile/edit/', ProfileUpdateView.as_view(), name='profile_edit'),
    path('category/<slug:slug>/', CategoryListingView.as_view(), name='category_listings'),
    path('seller/<int:pk>/', SellerListingView.as_view(), name='seller_listings'),
    path('listing/<int:pk>/', ListingDetailView.as_view(), name='listing_detail'),
    path('listing/<int:pk>/bid/', PlaceBidView.as_view(), name='place_bid'),
    path('invoice/<int:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
]
