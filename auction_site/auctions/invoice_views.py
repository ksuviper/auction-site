from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .invoice_forms import InvoiceCreateForm, InvoiceEditForm
from .models import Invoice, Seller


class StaffRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden('Staff access required.')
        return super().dispatch(request, *args, **kwargs)


class InvoiceDashboardView(StaffRequiredMixin, View):
    template_name = 'invoices/dashboard.html'

    def get(self, request):
        invoices = (
            Invoice.objects
            .select_related('listing', 'buyer', 'seller')
            .order_by('seller__name', '-created_at')
        )

        # Group by seller
        sellers_map = {}
        for invoice in invoices:
            key = invoice.seller_id
            if key not in sellers_map:
                sellers_map[key] = {'seller': invoice.seller, 'invoices': []}
            sellers_map[key]['invoices'].append(invoice)

        groups = list(sellers_map.values())

        return render(request, self.template_name, {'groups': groups})

    def post(self, request):
        action = request.POST.get('action')
        selected_ids = request.POST.getlist('selected_invoices')

        if not selected_ids:
            messages.warning(request, 'No invoices selected.')
            return redirect('invoice_dashboard')

        if action == 'mark_sent':
            updated = Invoice.objects.filter(pk__in=selected_ids).update(is_sent=True)
            messages.success(request, f'Marked {updated} invoice(s) as sent.')
        elif action == 'mark_unsent':
            updated = Invoice.objects.filter(pk__in=selected_ids).update(is_sent=False)
            messages.success(request, f'Marked {updated} invoice(s) as unsent.')
        else:
            messages.error(request, 'Unknown action.')

        return redirect('invoice_dashboard')


class InvoiceEditView(StaffRequiredMixin, View):
    template_name = 'invoices/edit.html'

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        form = InvoiceEditForm(instance=invoice)
        return render(request, self.template_name, {'form': form, 'invoice': invoice})

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        form = InvoiceEditForm(request.POST, instance=invoice)
        if form.is_valid():
            form.save()
            messages.success(request, f'Invoice #{invoice.pk} updated.')
            return redirect('invoice_dashboard')
        return render(request, self.template_name, {'form': form, 'invoice': invoice})


class InvoiceCreateView(StaffRequiredMixin, View):
    template_name = 'invoices/create.html'

    def get(self, request):
        form = InvoiceCreateForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = InvoiceCreateForm(request.POST)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.is_manually_created = True
            invoice.save()
            messages.success(request, f'Invoice #{invoice.pk} created.')
            return redirect('invoice_dashboard')
        return render(request, self.template_name, {'form': form})


class InvoiceDetailView(View):
    template_name = 'invoices/detail.html'

    def get(self, request, pk):
        invoice = get_object_or_404(
            Invoice.objects.select_related('listing', 'buyer', 'seller'),
            pk=pk,
        )
        is_owner = request.user.is_authenticated and invoice.buyer == request.user
        if not is_owner and not (request.user.is_authenticated and request.user.is_staff):
            return HttpResponseForbidden('You do not have permission to view this invoice.')
        return render(request, self.template_name, {'invoice': invoice})
