from django.urls import path

from .invoice_views import InvoiceCreateView, InvoiceDashboardView, InvoiceEditView

urlpatterns = [
    path('', InvoiceDashboardView.as_view(), name='invoice_dashboard'),
    path('<int:pk>/edit/', InvoiceEditView.as_view(), name='invoice_edit'),
    path('create/', InvoiceCreateView.as_view(), name='invoice_create'),
]
