"""
Staff views for generating, reviewing and sending combined invoices.

The workflow is deliberately three deliberate steps, not one:

  1. **Generate** gathers every unbilled sale into draft invoices, one per
     buyer+seller pair. Nothing leaves the site.
  2. **Review** shows a draft's line items and totals, and lets an admin
     override the shipping or apply a discount before anyone sees it.
  3. **Send** emails the buyer. This is the first and only thing the buyer
     hears about their wins, so it cannot happen by accident.
"""

from django.contrib import messages
from django.db.models import Count, Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .combined_invoice_forms import CombinedInvoiceReviewForm
from .mixins import StaffRequiredMixin
from .models import CombinedInvoice, Invoice
from .services import generate_combined_invoices, send_combined_invoice


def _drafts_and_sent():
    """
    Both lists for the dashboard, with line items and totals ready.

    Prefetching the line items matters: subtotal and total_shipping aggregate
    over them, so a page of twenty invoices would otherwise be forty queries.
    """
    queryset = (
        CombinedInvoice.objects
        .select_related('buyer', 'seller__profile')
        .prefetch_related(Prefetch('line_items', queryset=Invoice.objects.all()))
        .annotate(item_count=Count('line_items'))
    )
    return (
        queryset.filter(status='draft').order_by('seller__username', 'pk'),
        queryset.filter(status='sent').order_by('-sent_at'),
    )


class CombinedInvoiceDashboardView(StaffRequiredMixin, View):
    """Drafts awaiting review, sent invoices, and what is not yet billed."""

    template_name = 'combined_invoices/dashboard.html'

    def get(self, request):
        drafts, sent = _drafts_and_sent()

        # The count of unbilled sales is what tells an admin whether pressing
        # Generate would do anything at all.
        unbilled = Invoice.objects.filter(combined_invoice__isnull=True)
        unbilled_summary = unbilled.aggregate(
            items=Count('pk'), value=Sum('amount')
        )

        return render(request, self.template_name, {
            'drafts': drafts,
            'sent_invoices': sent,
            'unbilled_count': unbilled_summary['items'] or 0,
            'unbilled_value': unbilled_summary['value'] or 0,
            'unbilled_pairs': (
                unbilled.values('buyer_id', 'seller_id').distinct().count()
            ),
        })


class GenerateCombinedInvoicesView(StaffRequiredMixin, View):
    """POST-only: gathers unbilled sales into drafts. Sends nothing."""

    def post(self, request):
        drafts = generate_combined_invoices()

        if not drafts:
            messages.info(
                request,
                'Nothing to invoice — every sale is already on an invoice.',
            )
        else:
            messages.success(
                request,
                f'{len(drafts)} draft invoice(s) ready for review. Nothing has '
                'been emailed yet.',
            )
        return redirect('combined_invoice_dashboard')


class CombinedInvoiceReviewView(StaffRequiredMixin, View):
    """
    Review one invoice: adjust shipping, discount and notes, then send it.

    Saving and sending are separate submits of the same form, so the numbers an
    admin just typed are what gets sent — rather than sending whatever was last
    saved and silently discarding the edit on screen.
    """

    template_name = 'combined_invoices/review.html'

    def _get_invoice(self, pk):
        return get_object_or_404(
            CombinedInvoice.objects.select_related('buyer', 'seller__profile'),
            pk=pk,
        )

    def _context(self, combined_invoice, form):
        return {
            'invoice': combined_invoice,
            'form': form,
            'line_items': (
                combined_invoice.line_items
                .select_related('listing')
                .order_by('pk')
            ),
            'payment_options': (
                getattr(combined_invoice.seller, 'profile', None)
                and combined_invoice.seller.profile.payment_options
            ),
        }

    def get(self, request, pk):
        combined_invoice = self._get_invoice(pk)
        form = CombinedInvoiceReviewForm(instance=combined_invoice)
        return render(
            request, self.template_name, self._context(combined_invoice, form)
        )

    def post(self, request, pk):
        combined_invoice = self._get_invoice(pk)

        if combined_invoice.status != 'draft':
            messages.warning(
                request,
                f'Invoice #{combined_invoice.pk} has already been sent and '
                'cannot be changed.',
            )
            return redirect('combined_invoice_review', pk=combined_invoice.pk)

        form = CombinedInvoiceReviewForm(
            request.POST, instance=combined_invoice
        )
        if not form.is_valid():
            return render(
                request, self.template_name,
                self._context(combined_invoice, form),
            )

        combined_invoice = form.save()

        if request.POST.get('action') != 'send':
            messages.success(
                request,
                f'Invoice #{combined_invoice.pk} saved. Still a draft — the '
                'buyer has not been emailed.',
            )
            return redirect('combined_invoice_review', pk=combined_invoice.pk)

        if send_combined_invoice(combined_invoice):
            recipient = combined_invoice.buyer.email or 'no email on file'
            messages.success(
                request,
                f'Invoice #{combined_invoice.pk} sent to {recipient} '
                f'for ${combined_invoice.total}.',
            )
        else:
            messages.warning(
                request,
                f'Invoice #{combined_invoice.pk} was already sent; nothing '
                'was emailed again.',
            )
        return redirect('combined_invoice_dashboard')
