from django.urls import path

from .invoice_views import InvoiceDetailView
from .views import (
    AccountPendingApprovalView,
    BuyNowView,
    CategoryListingView,
    ListingDetailView,
    PlaceBidView,
    PlaceProxyBidView,
    PostCommentView,
    ProfileDetailView,
    ProfileUpdateView,
    SecuritySettingsView,
    SellerListingView,
    ToggleEmailLoginCodeView,
)

urlpatterns = [
    path(
        'accounts/pending-approval/',
        AccountPendingApprovalView.as_view(),
        name='account_pending_approval',
    ),
    path('profile/', ProfileDetailView.as_view(), name='profile'),
    path('profile/edit/', ProfileUpdateView.as_view(), name='profile_edit'),
    path('account/security/', SecuritySettingsView.as_view(), name='security_settings'),
    path(
        'account/security/toggle-email-code/',
        ToggleEmailLoginCodeView.as_view(),
        name='toggle_email_login_code',
    ),
    path('category/<slug:slug>/', CategoryListingView.as_view(), name='category_listings'),
    path('seller/<int:pk>/', SellerListingView.as_view(), name='seller_listings'),
    path('listing/<int:pk>/', ListingDetailView.as_view(), name='listing_detail'),
    path('listing/<int:pk>/bid/', PlaceBidView.as_view(), name='place_bid'),
    path('listing/<int:pk>/buy-now/', BuyNowView.as_view(), name='buy_now'),
    path('listing/<int:pk>/proxy-bid/', PlaceProxyBidView.as_view(), name='place_proxy_bid'),
    path('listing/<int:pk>/comment/', PostCommentView.as_view(), name='post_comment'),
    path('invoice/<int:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
]
