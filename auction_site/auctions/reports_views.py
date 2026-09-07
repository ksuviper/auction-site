import csv
import io
import json
from datetime import date

from django.db.models import Count, DecimalField, F, Sum
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.views import View

from .mixins import StaffRequiredMixin
from .models import Invoice
from .utils import seller_display_name


class ReportsView(StaffRequiredMixin, View):
    template_name = 'reports/dashboard.html'

    def get(self, request):
        # ── All-time summary ─────────────────────────────────────────────────
        # Units, not invoice rows: one Buy It Now invoice can cover several
        # plants. Counting invoices instead of summing quantity would under-report
        # every multi-unit sale.
        #
        # Sourced from Invoice rather than from listings-with-a-winner, which is
        # what this used to count. `winner` is only set for auction wins now, so
        # a listing-based count would silently drop every Buy It Now sale.
        total_sold = Invoice.objects.aggregate(
            units=Coalesce(Sum('quantity'), 0)
        )['units']

        total_revenue = Invoice.objects.aggregate(
            total=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )['total']

        # ── By year ──────────────────────────────────────────────────────────
        # Keyed off the invoice date so a sale counts in the year it happened,
        # and so buy_now sales are included at all.
        sales_by_year = dict(
            Invoice.objects
            .annotate(year=ExtractYear('created_at'))
            .values('year')
            .annotate(sold=Sum('quantity'))
            .values_list('year', 'sold')
        )
        revenue_by_year = dict(
            Invoice.objects
            .annotate(year=ExtractYear('created_at'))
            .values('year')
            .annotate(total=Sum('amount'))
            .values_list('year', 'total')
        )
        all_years = sorted(set(sales_by_year) | set(revenue_by_year), reverse=True)
        by_year = [
            {
                'year': y,
                'sold': sales_by_year.get(y, 0),
                'revenue': revenue_by_year.get(y, 0),
            }
            for y in all_years
        ]

        # ── By seller ────────────────────────────────────────────────────────
        # Grouped by account rather than by a seller's name, which is no longer
        # a column of its own — a User carries first/last name and username, and
        # the display name is assembled from them. Pulling all three keeps this
        # one query instead of a lookup per row.
        by_seller = list(
            Invoice.objects
            .values(
                'seller_id', 'seller__first_name', 'seller__last_name',
                'seller__username',
            )
            .annotate(
                count=Count('pk'),
                units=Coalesce(Sum('quantity'), 0),
                total=Sum('amount'),
            )
            .order_by('-total')
        )
        for row in by_seller:
            full_name = (
                f"{row['seller__first_name']} {row['seller__last_name']}".strip()
            )
            row['seller_name'] = full_name or row['seller__username']

        # ── By category ──────────────────────────────────────────────────────
        # Also moved onto Invoice. The old version grouped listings and summed
        # current_bid, which is 0 on a Buy It Now listing — so buy_now revenue
        # showed as zero even before `winner` stopped being set.
        by_category = list(
            Invoice.objects
            .filter(listing__isnull=False)
            .values(category__name=F('listing__category__name'))
            .annotate(
                count=Count('pk'),
                units=Coalesce(Sum('quantity'), 0),
                revenue=Sum('amount'),
            )
            .order_by('-revenue')
        )

        # ── Monthly revenue for current year (Chart.js) ──────────────────────
        current_year = date.today().year
        monthly_qs = (
            Invoice.objects
            .filter(created_at__year=current_year)
            .annotate(month=ExtractMonth('created_at'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        monthly_revenue = [0.0] * 12
        for row in monthly_qs:
            monthly_revenue[row['month'] - 1] = float(row['total'])

        return render(request, self.template_name, {
            'total_sold': total_sold,
            'total_revenue': total_revenue,
            'by_year': by_year,
            'by_seller': by_seller,
            'by_category': by_category,
            'current_year': current_year,
            'monthly_revenue_json': json.dumps(monthly_revenue),
        })


# ── CSV export ────────────────────────────────────────────────────────────────

_CSV_HEADERS = [
    'Invoice #', 'Buyer', 'Seller', 'Item', 'Quantity',
    'Amount', 'Shipping', 'Total', 'Payment Method', 'Sent', 'Date',
]


def _invoice_rows():
    yield _CSV_HEADERS
    qs = (
        Invoice.objects
        .select_related('listing', 'buyer', 'seller__profile')
        .order_by('-created_at')
        .iterator(chunk_size=500)
    )
    for inv in qs:
        yield [
            inv.pk,
            inv.buyer.username,
            seller_display_name(inv.seller),
            inv.item_display,
            # A Buy It Now invoice can cover several units, so the dollar
            # columns alone no longer say how many plants shipped.
            inv.quantity,
            inv.amount,
            inv.shipping_fee,
            inv.total,
            inv.payment_method or '',
            'Yes' if inv.is_sent else 'No',
            inv.created_at.strftime('%Y-%m-%d'),
        ]


class ExportInvoicesCSVView(StaffRequiredMixin, View):
    def get(self, request):
        def stream():
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in _invoice_rows():
                writer.writerow(row)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()

        response = StreamingHttpResponse(stream(), content_type='text/csv; charset=utf-8')
        filename = f'invoices_{date.today():%Y-%m-%d}.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
