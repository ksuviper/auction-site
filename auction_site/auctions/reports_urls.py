from django.urls import path

from .reports_views import ExportInvoicesCSVView, ReportsView

urlpatterns = [
    path('', ReportsView.as_view(), name='reports_dashboard'),
    path('export/csv/', ExportInvoicesCSVView.as_view(), name='reports_export_csv'),
]
