from django.urls import path

from .combined_invoice_views import (
    CombinedInvoiceDashboardView,
    CombinedInvoiceReviewView,
    GenerateCombinedInvoicesView,
)

urlpatterns = [
    path('', CombinedInvoiceDashboardView.as_view(), name='combined_invoice_dashboard'),
    path(
        'generate/',
        GenerateCombinedInvoicesView.as_view(),
        name='combined_invoice_generate',
    ),
    path(
        '<int:pk>/review/',
        CombinedInvoiceReviewView.as_view(),
        name='combined_invoice_review',
    ),
]
