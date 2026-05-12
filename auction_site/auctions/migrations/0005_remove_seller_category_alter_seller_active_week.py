# Generated manually on 2026-05-09

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auctions', '0004_invoice_item_description_alter_invoice_listing_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='seller',
            name='category',
        ),
        migrations.AlterField(
            model_name='seller',
            name='active_week',
            field=models.DateField(
                null=True,
                blank=True,
                help_text='Start date of the week this seller is active',
            ),
        ),
    ]
