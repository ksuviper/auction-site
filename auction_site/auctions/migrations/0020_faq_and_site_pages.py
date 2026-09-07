"""
Add the FAQ and the admin-editable content pages, and seed the three pages.

Terms of Service and About Us are seeded as placeholders for an admin to
replace. The Privacy Policy is seeded with the *real* text that used to live in
legal/privacy_policy.html: it is a substantive policy the Google and Facebook
OAuth apps were pointed at, so replacing it with "lorem ipsum" would have taken
a live legal page down. /privacy/ now redirects to /legal/privacy-policy/, so
that address keeps working with one editable source behind it.
"""

from django.db import migrations, models

PRIVACY_BODY = """\
<p class="text-muted">Last updated: May 2026</p>

<h2 class="h5 fw-bold mb-3">Who We Are</h2>
<p>ASQ Daylily Auctions ("we", "us", or "our") operates the auction website at
  <strong>asqdaylilies.com</strong>. We facilitate online auctions for daylily plants
  between sellers and buyers in the United States.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Information We Collect</h2>
<p>When you create an account or place a bid, we may collect:</p>
<ul>
  <li>Name and email address</li>
  <li>Mailing/shipping address</li>
  <li>Phone number (optional)</li>
  <li>Bid history and auction activity</li>
  <li>Information from social login providers (Google, Facebook) if you choose to sign in that way</li>
</ul>

<h2 class="h5 fw-bold mt-4 mb-3">How We Use Your Information</h2>
<ul>
  <li>To operate the auction platform and process bids</li>
  <li>To contact you about auctions you've won or participated in</li>
  <li>To send invoices and facilitate payment between buyers and sellers</li>
  <li>To send service-related emails (auction end notifications, account alerts)</li>
</ul>

<h2 class="h5 fw-bold mt-4 mb-3">Sharing Your Information</h2>
<p>We do not sell your personal information. We share your contact details with the
  seller only when you win an auction, so they can arrange shipping and payment.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Social Login (Google &amp; Facebook)</h2>
<p>If you sign in with Google or Facebook, we receive your name and email address
  from that provider. We do not receive your password or payment information from them.
  Your use of those services is subject to their respective privacy policies.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Data Retention</h2>
<p>We retain your account information for as long as your account is active. Bid and
  invoice records are kept for accounting purposes. You may request deletion of your
  account at any time — see our
  <a href="/privacy/data-deletion/">Data Deletion page</a>.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Cookies</h2>
<p>We use session cookies to keep you logged in. We do not use tracking or advertising
  cookies.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Contact Us</h2>
<p>Questions about this policy? Email us at
  <a href="mailto:asqdaylilies@gmail.com">asqdaylilies@gmail.com</a>.</p>
"""

TERMS_BODY = """\
<p class="text-muted">This is placeholder text. Replace it in the admin under
  <strong>Site pages</strong>.</p>

<h2 class="h5 fw-bold mb-3">Bidding</h2>
<p>A bid is a commitment to buy. Winning bidders are expected to pay the seller
  promptly using one of that seller's accepted payment methods.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Payment and Shipping</h2>
<p>Payment is arranged directly between buyer and seller. Shipping costs are
  shown on each listing before you bid or buy.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Membership</h2>
<p>An active membership is required to place bids or make purchases.</p>

<h2 class="h5 fw-bold mt-4 mb-3">Questions</h2>
<p>Email us at <a href="mailto:asqdaylilies@gmail.com">asqdaylilies@gmail.com</a>.</p>
"""

ABOUT_BODY = """\
<p class="text-muted">This is placeholder text. Replace it in the admin under
  <strong>Site pages</strong>. The opening paragraphs also appear on the
  homepage, so put the short version first.</p>

<p>The Above Status Quo Daylily Auction Group brings together growers and
  collectors from across the country. Each week we feature a different seller,
  whose plants are listed for auction or at a fixed price.</p>

<p>We are a small group run by daylily people, for daylily people. Payment goes
  directly to the grower, and every plant ships from the person who grew it.</p>
"""

SEEDED_PAGES = [
    ('privacy-policy', 'Privacy Policy', PRIVACY_BODY),
    ('terms-of-service', 'Terms of Service', TERMS_BODY),
    ('about-us', 'About Us', ABOUT_BODY),
]


def seed_site_pages(apps, schema_editor):
    """
    Create the three pages the site links to.

    get_or_create rather than create: this migration must be safe to re-run
    against a database where an admin has already made one of these by hand,
    and must never overwrite text they have edited.
    """
    SitePage = apps.get_model('auctions', 'SitePage')
    for slug, title, body in SEEDED_PAGES:
        SitePage.objects.get_or_create(
            slug=slug, defaults={'title': title, 'body': body}
        )


def unseed_site_pages(apps, schema_editor):
    """
    Reversing drops the seeded rows.

    Only reachable by migrating backwards past this point, which also deletes
    the table — so there is nothing to preserve by being cleverer here.
    """
    SitePage = apps.get_model('auctions', 'SitePage')
    SitePage.objects.filter(slug__in=[slug for slug, _, _ in SEEDED_PAGES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0019_seller_payment_methods"),
    ]

    operations = [
        migrations.CreateModel(
            name="FAQItem",
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
                ("question", models.CharField(max_length=255)),
                (
                    "answer",
                    models.TextField(
                        help_text="Plain text is fine — line breaks are kept as you type them. HTML also works if you want a link."
                    ),
                ),
                (
                    "display_order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Lower numbers appear first. Ties fall back to the question.",
                    ),
                ),
                (
                    "is_published",
                    models.BooleanField(
                        default=True,
                        help_text="Untick to keep this question off the public page.",
                    ),
                ),
            ],
            options={
                "verbose_name": "FAQ item",
                "ordering": ["display_order", "question"],
            },
        ),
        migrations.CreateModel(
            name="SitePage",
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
                    "slug",
                    models.SlugField(
                        help_text='Appears in the page URL, e.g. "terms-of-service" is served at /legal/terms-of-service/. Changing it changes the address, so existing links to this page will break.',
                        max_length=100,
                        unique=True,
                    ),
                ),
                ("title", models.CharField(max_length=200)),
                (
                    "body",
                    models.TextField(
                        help_text="HTML is allowed, so headings (<h2>), lists (<ul><li>) and links work. Text with no markup keeps its line breaks."
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["title"],
            },
        ),
        migrations.RunPython(seed_site_pages, unseed_site_pages),
    ]
