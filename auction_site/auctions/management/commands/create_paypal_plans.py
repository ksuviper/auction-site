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

        # (audience, billing cycle, PayPal plan name, interval, price source)
        wanted = [
            ('buyer', 'monthly', 'ASQ Daylily Monthly Membership', 'MONTH',
             settings.PAYPAL_MONTHLY_PRICE, 'PAYPAL_MONTHLY_PLAN_ID'),
            ('buyer', 'yearly', 'ASQ Daylily Annual Membership', 'YEAR',
             settings.PAYPAL_YEARLY_PRICE, 'PAYPAL_YEARLY_PLAN_ID'),
            ('seller', 'monthly', 'ASQ Daylily Seller Monthly Membership',
             'MONTH', settings.PAYPAL_SELLER_MONTHLY_PRICE,
             'PAYPAL_SELLER_MONTHLY_PLAN_ID'),
            ('seller', 'yearly', 'ASQ Daylily Seller Annual Membership',
             'YEAR', settings.PAYPAL_SELLER_YEARLY_PRICE,
             'PAYPAL_SELLER_YEARLY_PLAN_ID'),
        ]

        created = []
        skipped = []
        for audience, cycle, name, interval, raw_price, env_name in wanted:
            price = self._price(audience, cycle, raw_price)
            if price is None:
                # Seller pricing may not have been decided yet. Creating a plan
                # at a made-up figure would put a real, chargeable price in
                # front of sellers, so skip it and say so.
                skipped.append((name, env_name))
                continue

            plan_id = self._create_plan(
                token, product_id, name=name, interval_unit=interval,
                price=price,
            )
            self._record(audience, cycle, price, plan_id)
            created.append((env_name, plan_id))

        self.stdout.write('')
        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    'Plans created, and Membership Pricing in the admin now '
                    'holds each price and plan ID. These lines are only needed '
                    'if you prefer to keep the .env in step as well:'
                )
            )
            for env_name, plan_id in created:
                self.stdout.write(f'{env_name}={plan_id}')

        for name, env_name in skipped:
            self.stdout.write(
                self.style.WARNING(
                    f'Skipped "{name}" — no price set. Put one in the matching '
                    f'price env var and re-run, or set it under Membership '
                    f'Pricing in the admin and run this again to create '
                    f'{env_name}.'
                )
            )

    @staticmethod
    def _price(audience, cycle, raw_price):
        """
        The price to create this plan at: the env var, else the admin row.

        Checking the database too means somebody who has set seller pricing in
        the admin does not also have to put it in the environment just to get
        the PayPal plan made.
        """
        from decimal import Decimal, InvalidOperation

        from auctions.models import SubscriptionPlan

        if raw_price not in (None, ''):
            try:
                return Decimal(str(raw_price))
            except (InvalidOperation, TypeError, ValueError):
                pass

        row = SubscriptionPlan.objects.filter(
            audience=audience, billing_cycle=cycle
        ).first()
        if row is not None and row.price > 0:
            return row.price
        return None

    @staticmethod
    def _record(audience, cycle, price, plan_id):
        """
        Write the plan back to the admin table.

        The command used to end at printing an ID for somebody to paste into a
        .env by hand, which is a step to forget. The database is what the site
        reads now, so this keeps it authoritative and leaves the printed lines
        as a convenience.
        """
        from auctions.models import SubscriptionPlan

        SubscriptionPlan.objects.update_or_create(
            audience=audience,
            billing_cycle=cycle,
            defaults={
                'price': price,
                'paypal_plan_id': plan_id,
                'is_active': True,
            },
        )

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
