from django.urls import path

from .subscription_views import (
    MembershipCancelView,
    MembershipView,
    PayPalWebhookView,
    SubscribeCancelledView,
    SubscribeCreateView,
    SubscribeLandingView,
    SubscribeReturnView,
)

urlpatterns = [
    path('subscribe/', SubscribeLandingView.as_view(), name='subscribe'),
    path('subscribe/create/<str:plan>/', SubscribeCreateView.as_view(), name='subscribe_create'),
    path('subscribe/return/', SubscribeReturnView.as_view(), name='subscribe_return'),
    path('subscribe/cancelled/', SubscribeCancelledView.as_view(), name='subscribe_cancelled'),
    path('account/membership/', MembershipView.as_view(), name='membership'),
    path('account/membership/cancel/', MembershipCancelView.as_view(), name='membership_cancel'),
    path('paypal/webhook/', PayPalWebhookView.as_view(), name='paypal_webhook'),
]
