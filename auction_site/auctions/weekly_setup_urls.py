from django.urls import path

from .weekly_setup_views import (
    NotifyFollowersView,
    WeeklyListingCreateView,
    WeeklySetupDoneView,
    WeeklySetupSellerView,
)

urlpatterns = [
    path('', WeeklySetupSellerView.as_view(), name='weekly_setup'),
    # Name kept as weekly_setup_listings so the step-1 redirect and the links on
    # the done page keep working; the page itself is now one listing at a time.
    path(
        '<int:seller_pk>/add-listing/',
        WeeklyListingCreateView.as_view(),
        name='weekly_setup_listings',
    ),
    path('<int:seller_pk>/done/', WeeklySetupDoneView.as_view(), name='weekly_setup_done'),
    path(
        '<int:seller_pk>/notify-followers/',
        NotifyFollowersView.as_view(),
        name='weekly_setup_notify_followers',
    ),
]
