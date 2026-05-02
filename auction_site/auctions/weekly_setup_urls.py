from django.urls import path

from .weekly_setup_views import WeeklySetupDoneView, WeeklySetupListingsView, WeeklySetupSellerView

urlpatterns = [
    path('', WeeklySetupSellerView.as_view(), name='weekly_setup'),
    path('<int:seller_pk>/listings/', WeeklySetupListingsView.as_view(), name='weekly_setup_listings'),
    path('<int:seller_pk>/done/', WeeklySetupDoneView.as_view(), name='weekly_setup_done'),
]
