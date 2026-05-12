# Generated manually on 2026-05-11

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auctions', '0005_remove_seller_category_alter_seller_active_week'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='country',
            field=models.CharField(
                blank=True,
                default='',
                help_text='2-letter country code, e.g. US',
                max_length=2,
            ),
        ),
    ]
