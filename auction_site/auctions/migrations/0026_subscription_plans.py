"""
Move membership pricing out of environment variables and into the database.

Creates the four plan rows — buyer and seller, monthly and yearly — seeded from
whatever the environment already holds, so an existing deployment keeps the
prices and PayPal plan IDs it is running on and nobody has to retype them.

The seller rows are seeded with no price and no PayPal plan ID, because seller
pricing has not been decided. They are marked inactive until someone sets a
price and runs `manage.py create_paypal_plans`, and until then the subscribe
page tells a seller that seller memberships are not available yet rather than
offering a checkout that cannot complete.
"""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import migrations, models


def _money(raw):
    """A price from the environment, or None if it is unset or not a number."""
    if raw in (None, ''):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def seed_plans(apps, schema_editor):
    SubscriptionPlan = apps.get_model('auctions', 'SubscriptionPlan')

    rows = [
        ('buyer', 'monthly',
         getattr(settings, 'PAYPAL_MONTHLY_PRICE', None),
         getattr(settings, 'PAYPAL_MONTHLY_PLAN_ID', '')),
        ('buyer', 'yearly',
         getattr(settings, 'PAYPAL_YEARLY_PRICE', None),
         getattr(settings, 'PAYPAL_YEARLY_PLAN_ID', '')),
        ('seller', 'monthly',
         getattr(settings, 'PAYPAL_SELLER_MONTHLY_PRICE', None),
         getattr(settings, 'PAYPAL_SELLER_MONTHLY_PLAN_ID', '')),
        ('seller', 'yearly',
         getattr(settings, 'PAYPAL_SELLER_YEARLY_PRICE', None),
         getattr(settings, 'PAYPAL_SELLER_YEARLY_PLAN_ID', '')),
    ]

    for audience, cycle, raw_price, plan_id in rows:
        price = _money(raw_price)
        SubscriptionPlan.objects.get_or_create(
            audience=audience,
            billing_cycle=cycle,
            defaults={
                # price is non-null, so an unpriced plan is stored at zero and
                # held inactive rather than left half-created.
                'price': price if price is not None else Decimal('0.00'),
                'paypal_plan_id': plan_id or '',
                'currency': 'USD',
                'is_active': price is not None and bool(plan_id),
            },
        )


def drop_plans(apps, schema_editor):
    """Reversing drops the rows; the table goes with the CreateModel."""
    apps.get_model('auctions', 'SubscriptionPlan').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0025_home_page_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="plan_audience",
            field=models.CharField(
                choices=[("buyer", "Buyer"), ("seller", "Seller")],
                default="buyer",
                help_text="Which rate this member signed up on, recorded from their seller flag at the time. For reporting only — it does not affect what the membership lets them do, and flagging someone a seller later does not change a membership they already hold.",
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "audience",
                    models.CharField(
                        choices=[("buyer", "Buyer"), ("seller", "Seller")],
                        max_length=10,
                    ),
                ),
                (
                    "billing_cycle",
                    models.CharField(
                        choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
                        max_length=10,
                    ),
                ),
                (
                    "price",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Changing this pushes the new price to PayPal before it is saved. Existing members are repriced from their next billing date.",
                        max_digits=8,
                    ),
                ),
                ("currency", models.CharField(default="USD", max_length=3)),
                (
                    "paypal_plan_id",
                    models.CharField(
                        blank=True,
                        help_text="From `manage.py create_paypal_plans`. Without it there is nothing to sell and nothing to sync, and this plan is treated as unavailable.",
                        max_length=100,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Untick to stop offering this plan on the subscribe page.",
                    ),
                ),
                (
                    "last_synced_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When this price was last accepted by PayPal.",
                        null=True,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Subscription plan",
                "ordering": ["audience", "billing_cycle"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("audience", "billing_cycle"),
                        name="one_plan_per_audience_and_cycle",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_plans, drop_plans),
    ]
