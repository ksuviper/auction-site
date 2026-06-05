"""
Management command: create_paypal_plans

Creates the PayPal catalog product and the monthly + yearly billing plans used
for ASQ Daylily memberships, then prints the plan IDs so they can be copied into
PAYPAL_MONTHLY_PLAN_ID / PAYPAL_YEARLY_PLAN_ID in .env.

Usage:
    python manage.py create_paypal_plans

Requires PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and PAYPAL_MODE in the env.
The Subscriptions API requires a catalog product before plans can be created,
so this command creates one product and attaches both plans to it.

Reference: https://developer.paypal.com/docs/subscriptions/
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from auctions.paypal import PayPalError, get_access_token, paypal_request


class Command(BaseCommand):
    help = 'Create the PayPal product and monthly/yearly billing plans for memberships.'

    def handle(self, *args, **options):
        try:
            token = get_access_token()
        except PayPalError as exc:
            raise CommandError(str(exc))

        self.stdout.write(f'Using PayPal mode: {settings.PAYPAL_MODE}')

        product_id = self._create_product(token)
        self.stdout.write(self.style.SUCCESS(f'Product created: {product_id}'))

        monthly_id = self._create_plan(
            token,
            product_id,
            name='ASQ Daylily Monthly Membership',
            interval_unit='MONTH',
            price=settings.PAYPAL_MONTHLY_PRICE,
        )
        yearly_id = self._create_plan(
            token,
            product_id,
            name='ASQ Daylily Annual Membership',
            interval_unit='YEAR',
            price=settings.PAYPAL_YEARLY_PRICE,
        )

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS('Plans created. Copy these into your .env:')
        )
        self.stdout.write(f'PAYPAL_MONTHLY_PLAN_ID={monthly_id}')
        self.stdout.write(f'PAYPAL_YEARLY_PLAN_ID={yearly_id}')

    def _create_product(self, token):
        resp = paypal_request(
            'POST',
            '/v1/catalogs/products',
            token=token,
            json={
                'name': 'ASQ Daylily Auctions Membership',
                'description': (
                    'Membership granting bidding and purchasing access on '
                    'ASQ Daylily Auctions.'
                ),
                'type': 'SERVICE',
                'category': 'MEMBERSHIP_CLUBS_AND_ORGANIZATIONS',
            },
        )
        if resp.status_code not in (200, 201):
            raise CommandError(
                f'Failed to create product ({resp.status_code}): {resp.text}'
            )
        return resp.json()['id']

    def _create_plan(self, token, product_id, name, interval_unit, price):
        resp = paypal_request(
            'POST',
            '/v1/billing/plans',
            token=token,
            json={
                'product_id': product_id,
                'name': name,
                'status': 'ACTIVE',
                'billing_cycles': [
                    {
                        'frequency': {
                            'interval_unit': interval_unit,
                            'interval_count': 1,
                        },
                        'tenure_type': 'REGULAR',
                        'sequence': 1,
                        'total_cycles': 0,  # 0 == infinite / until cancelled
                        'pricing_scheme': {
                            'fixed_price': {
                                'value': str(price),
                                'currency_code': 'USD',
                            }
                        },
                    }
                ],
                'payment_preferences': {
                    'auto_bill_outstanding': True,
                    'setup_fee_failure_action': 'CONTINUE',
                    'payment_failure_threshold': 1,
                },
            },
        )
        if resp.status_code not in (200, 201):
            raise CommandError(
                f'Failed to create plan "{name}" ({resp.status_code}): {resp.text}'
            )
        plan_id = resp.json()['id']
        self.stdout.write(self.style.SUCCESS(f'{name}: {plan_id}'))
        return plan_id
