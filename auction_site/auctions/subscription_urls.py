from django.urls import path

from .subscription_views import (
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
    path('paypal/webhook/', PayPalWebhookView.as_view(), name='paypal_webhook'),
]
