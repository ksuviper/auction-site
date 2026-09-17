"""
Move the homepage's wording out of the template and into the admin.

The welcome and the heading above the steps become SitePage rows; the three
step cards become HomePageStep rows. Everything is seeded with exactly what
index.html had hardcoded, so the page is unchanged until somebody edits it.

get_or_create throughout, so re-running this never duplicates a row and never
overwrites wording an admin has since changed.
"""

from django.db import migrations, models

WELCOME_TITLE = 'Welcome to ASQ Daylily Auctions'
WELCOME_BODY = (
    '<p class="mb-0">Discover and bid on premium daylilies from growers '
    'across the country. New sellers are featured each week.</p>'
)

STEPS_TITLE = 'How It Works'

STEPS = [
    (
        'fas fa-search',
        'Browse',
        'Use the category sidebar to find daylilies by type and seller.',
    ),
    (
        'fas fa-gavel',
        'Bid',
        'Place bids on plants you love. The highest bid wins when time runs '
        'out.',
    ),
    (
        'fas fa-check-circle',
        'Win & Enjoy',
        'Pay the seller directly using their preferred payment method.',
    ),
]


def seed(apps, schema_editor):
    SitePage = apps.get_model('auctions', 'SitePage')
    HomePageStep = apps.get_model('auctions', 'HomePageStep')

    SitePage.objects.get_or_create(
        slug='home',
        defaults={'title': WELCOME_TITLE, 'body': WELCOME_BODY},
    )
    # Body left empty: the heading is all the page showed. An admin can add a
    # line under it later without anything here needing to change.
    SitePage.objects.get_or_create(
        slug='home-how-it-works',
        defaults={'title': STEPS_TITLE, 'body': ''},
    )

    for position, (icon, title, body) in enumerate(STEPS, start=1):
        HomePageStep.objects.get_or_create(
            title=title,
            defaults={
                'icon': icon,
                'body': body,
                'display_order': position * 10,
                'is_published': True,
            },
        )


def unseed(apps, schema_editor):
    """
    Drop only the rows this migration introduced.

    The steps table goes with the CreateModel either way; the two SitePage rows
    would otherwise be left behind as orphans with no page to appear on.
    """
    SitePage = apps.get_model('auctions', 'SitePage')
    SitePage.objects.filter(slug__in=('home', 'home-how-it-works')).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0024_site_header_text"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomePageStep",
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
                    "icon",
                    models.CharField(
                        blank=True,
                        default="fas fa-leaf",
                        help_text='Font Awesome class for the picture above the step, e.g. "fas fa-search", "fas fa-gavel", "fas fa-check-circle". Browse them at fontawesome.com/icons. Leave empty for no picture.',
                        max_length=60,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        help_text="The step's short name.", max_length=100
                    ),
                ),
                (
                    "body",
                    models.TextField(help_text="One or two sentences under the title."),
                ),
                (
                    "display_order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Lower numbers appear first. Ties fall back to the title.",
                    ),
                ),
                (
                    "is_published",
                    models.BooleanField(
                        default=True,
                        help_text="Untick to keep this step off the homepage.",
                    ),
                ),
            ],
            options={
                "verbose_name": "How It Works step",
                "verbose_name_plural": "How It Works steps",
                "ordering": ["display_order", "title"],
            },
        ),
        migrations.RunPython(seed, unseed),
    ]
