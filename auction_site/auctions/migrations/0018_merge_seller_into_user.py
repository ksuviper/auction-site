"""
Merge the standalone Seller model into User accounts.

A "seller" is now a User whose profile has is_seller ticked. Seller-specific
details move onto UserProfile, and AuctionListing.seller / Invoice.seller point
at User instead of at a Seller row.

    ⚠️  THIS MIGRATION DELETES DATA.  ⚠️

Every listing, bid, proxy bid, listing comment and invoice is deleted, along
with the Seller table itself. That is deliberate and was confirmed before this
was written: there is no sound automatic mapping from a free-text Seller.name to
a User account, and inventing accounts to preserve the rows would create
unusable logins that the approval and email-verification gates would then block.

User accounts, categories, subscriptions and wishlists are untouched.

Take a database backup before applying this to a live site. Deleting the rows is
what makes the FK columns re-pointable — a non-null seller cannot be introduced
while rows exist that have no User to point at — so the wipe runs first, in FK
order, in the same migration.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def wipe_auction_data(apps, schema_editor):
    """
    Clear everything that hangs off a listing, deepest dependency first.

    Explicit and ordered rather than relying on cascades: Invoice.listing and
    AuctionListing.category are PROTECT, so a wrong order raises instead of
    quietly deleting, and an explicit list makes the blast radius reviewable.
    """
    for label in (
        'Bid', 'ProxyBid', 'ListingComment', 'Invoice', 'AuctionListing',
    ):
        model = apps.get_model('auctions', label)
        deleted, _ = model.objects.all().delete()
        # Only when there was something to lose: on a fresh database (every
        # test run creates one) this migration has no work to do, and saying so
        # five times per run buries anything that matters.
        if deleted:
            print(f'  0018: deleted {deleted} {label} row(s)')


def no_restore(apps, schema_editor):
    """
    Reversing gets the schema back, never the deleted rows.

    Deliberately a no-op rather than an error: blocking the reverse would leave
    an operator unable to unwind the schema change at all, and the docstring
    above is where the warning belongs.
    """
    return None


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('auctions', '0017_auctionlisting_shipping_fee'),
    ]

    operations = [
        # ── 1. Seller details land on UserProfile ────────────────────────────
        migrations.AddField(
            model_name='userprofile',
            name='is_seller',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'This account sells plants. Seller-flagged users can be '
                    'chosen when adding a listing, appear on the public '
                    'category and seller pages, and get a read-only sales '
                    'dashboard.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_bio',
            field=models.TextField(
                blank=True, help_text="Shown on this seller's public page."
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_shipping_fee',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=7, null=True,
                help_text=(
                    "This seller's standard shipping charge. Used for any of "
                    'their listings that does not set its own.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_payment_methods',
            field=models.TextField(blank=True, help_text='e.g. PayPal, Venmo, Zelle'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sellers',
                to='auctions.auctioncategory',
                help_text='The category this seller primarily lists under.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_active_week',
            field=models.DateField(
                blank=True, null=True,
                help_text='Start date of the week this seller is active.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='seller_notify_on_comments',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Email this seller when a buyer posts a question on their '
                    'listing.'
                ),
            ),
        ),

        # ── 2. Clear the rows that point at Seller ───────────────────────────
        migrations.RunPython(wipe_auction_data, no_restore),

        # ── 3. Re-point the seller columns at User ───────────────────────────
        migrations.AlterField(
            model_name='auctionlisting',
            name='seller',
            field=models.ForeignKey(
                limit_choices_to={'profile__is_seller': True},
                on_delete=django.db.models.deletion.PROTECT,
                related_name='listings',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='seller',
            field=models.ForeignKey(
                limit_choices_to={'profile__is_seller': True},
                on_delete=django.db.models.deletion.PROTECT,
                related_name='invoices_as_seller',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # ── 4. And the model itself is gone ──────────────────────────────────
        migrations.DeleteModel(name='Seller'),
    ]
