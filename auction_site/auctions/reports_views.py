import csv
import io
import json
from datetime import date

from django.db.models import Count, DecimalField, Sum
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear
from django.http import HttpResponseForbidden, StreamingHttpResponse
from django.shortcuts import render
from django.views import View

from .models import AuctionListing, Invoice


class StaffRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden('Staff access required.')
        return super().dispatch(request, *args, **kwargs)


class ReportsView(StaffRequiredMixin, View):
    template_name = 'reports/dashboard.html'

    def get(self, request):
        # ── All-time summary ─────────────────────────────────────────────────
        total_sold = AuctionListing.objects.filter(
            is_closed=True, winner__isnull=False
        ).count()

        total_revenue = Invoice.objects.aggregate(
            total=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )['total']

        # ── By year ──────────────────────────────────────────────────────────
        sales_by_year = dict(
            AuctionListing.objects
            .filter(is_closed=True, winner__isnull=False)
            .annotate(year=ExtractYear('ends_at'))
            .values('year')
            .annotate(sold=Count('pk'))
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
        by_seller = list(
            Invoice.objects
            .values('seller__name')
            .annotate(count=Count('pk'), total=Sum('amount'))
            .order_by('-total')
        )

        # ── By category ──────────────────────────────────────────────────────
        by_category = list(
            AuctionListing.objects
            .filter(is_closed=True, winner__isnull=False)
            .values('category__name')
            .annotate(count=Count('pk'), revenue=Sum('current_bid'))
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
    'Invoice #', 'Buyer', 'Seller', 'Item',
    'Amount', 'Shipping', 'Total', 'Payment Method', 'Sent', 'Date',
]


def _invoice_rows():
    yield _CSV_HEADERS
    qs = (
        Invoice.objects
        .select_related('listing', 'buyer', 'seller')
        .order_by('-created_at')
        .iterator(chunk_size=500)
    )
    for inv in qs:
        yield [
            inv.pk,
            inv.buyer.username,
            inv.seller.name,
            inv.item_display,
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
