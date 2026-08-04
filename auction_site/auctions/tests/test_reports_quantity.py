"""Reporting must count units, not invoice rows.

One Buy It Now invoice can now cover several plants, and `winner` is no longer
set for buy_now sales — so the listing-based counts these reports used to do
would have dropped every Buy It Now sale entirely.
"""

import csv
import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Invoice,
    Seller,
)

User = get_user_model()


class ReportQuantityTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'reporter', 'reporter@example.com', 'pw', is_staff=True
        )
        self.client.force_login(self.staff)

        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = Seller.objects.create(
            name='Report Seller',
            accepted_payment_methods='PayPal',
            shipping_fee='5.00',
        )
        self.buyer = User.objects.create_user('rbuyer', 'rbuyer@example.com', 'pw')

    def _listing(self, listing_type='buy_now', quantity=10):
        now = timezone.now()
        return AuctionListing.objects.create(
            title=f'{listing_type} listing',
            category=self.category,
            seller=self.seller,
            start_price='1.00',
            listing_type=listing_type,
            buy_now_price='10.00' if listing_type == 'buy_now' else None,
            quantity_available=quantity if listing_type == 'buy_now' else None,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )

    def _invoice(self, listing, quantity, amount):
        return Invoice.objects.create(
            listing=listing,
            buyer=self.buyer,
            seller=self.seller,
            quantity=quantity,
            amount=amount,
            shipping_fee='5.00',
        )

    def test_plants_sold_sums_quantity_not_invoice_rows(self):
        listing = self._listing()
        self._invoice(listing, 3, '30.00')
        self._invoice(listing, 2, '20.00')

        response = self.client.get(reverse('reports_dashboard'))

        # Two invoices, five plants.
        self.assertEqual(response.context['total_sold'], 5)

    def test_buy_now_sales_are_counted_despite_having_no_winner(self):
        listing = self._listing()
        self._invoice(listing, 4, '40.00')
        self.assertIsNone(listing.winner)

        response = self.client.get(reverse('reports_dashboard'))

        self.assertEqual(response.context['total_sold'], 4)

    def test_auction_invoices_still_count_as_one_each(self):
        auction = self._listing(listing_type='auction')
        auction.winner = self.buyer
        auction.save(update_fields=['winner'])
        self._invoice(auction, 1, '18.00')

        response = self.client.get(reverse('reports_dashboard'))

        self.assertEqual(response.context['total_sold'], 1)

    def test_revenue_is_not_double_counted(self):
        listing = self._listing()
        self._invoice(listing, 3, '30.00')

        response = self.client.get(reverse('reports_dashboard'))

        # amount already covers quantity, so revenue is the invoice total.
        self.assertEqual(float(response.context['total_revenue']), 30.0)

    def test_by_seller_reports_units_and_invoice_count_separately(self):
        listing = self._listing()
        self._invoice(listing, 3, '30.00')
        self._invoice(listing, 1, '10.00')

        response = self.client.get(reverse('reports_dashboard'))
        row = response.context['by_seller'][0]

        self.assertEqual(row['count'], 2)
        self.assertEqual(row['units'], 4)
        self.assertEqual(float(row['total']), 40.0)

    def test_by_category_uses_invoice_amounts(self):
        listing = self._listing()
        self._invoice(listing, 3, '30.00')

        response = self.client.get(reverse('reports_dashboard'))
        row = response.context['by_category'][0]

        self.assertEqual(row['category__name'], 'Daylilies')
        self.assertEqual(row['units'], 3)
        self.assertEqual(float(row['revenue']), 30.0)

    def test_manual_invoices_without_a_listing_do_not_break_category_rows(self):
        Invoice.objects.create(
            item_description='Off-platform sale',
            buyer=self.buyer,
            seller=self.seller,
            quantity=2,
            amount='15.00',
            shipping_fee='0.00',
            is_manually_created=True,
        )

        response = self.client.get(reverse('reports_dashboard'))

        self.assertEqual(response.status_code, 200)
        # Counted in the all-time total, but has no category to group under.
        self.assertEqual(response.context['total_sold'], 2)
        self.assertEqual(response.context['by_category'], [])

    def test_csv_export_includes_a_quantity_column(self):
        listing = self._listing()
        self._invoice(listing, 3, '30.00')

        response = self.client.get(reverse('reports_export_csv'))
        body = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(body)))

        self.assertIn('Quantity', rows[0])
        quantity_index = rows[0].index('Quantity')
        self.assertEqual(rows[1][quantity_index], '3')

    def test_dashboard_renders_with_no_data(self):
        response = self.client.get(reverse('reports_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_sold'], 0)
