"""
Move the header's name and slogan out of the template and into the admin.

Adds a third line as well, empty and unused for now.

Safe on a live site and needs no follow-up: the defaults are the exact wording
base.html had hardcoded, so the single settings row picks them up and the header
looks the same the moment this is applied.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0023_site_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="site_extra_line",
            field=models.CharField(
                blank=True,
                default="",
                help_text="An optional third line under the slogan. Nothing is shown while this is empty.",
                max_length=200,
                verbose_name="Extra line",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="site_name",
            field=models.CharField(
                blank=True,
                default="Above Status Quo",
                help_text="The large first line beside the header image. Leave empty to show no name — useful when the image already has it.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="site_slogan",
            field=models.CharField(
                blank=True,
                default="Daylily Auction Group",
                help_text="The smaller second line, under the name.",
                max_length=200,
            ),
        ),
    ]
