from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

User = get_user_model()


class AuctionCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Auction categories'

    def save(self, *args, **kwargs):
        self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone_number = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(
        max_length=2,
        blank=True,
        default='',
        help_text='2-letter country code, e.g. US',
    )
    notes = models.TextField(blank=True)
    subscription_required = models.BooleanField(
        default=True,
        help_text='Uncheck to allow this user to bid without a PayPal subscription (admin override).',
    )
    is_approved = models.BooleanField(
        default=False,
        help_text='Admin must approve new accounts before they can log in.',
    )
    email_login_code_enabled = models.BooleanField(
        default=True,
        help_text=(
            'Require an emailed one-time code at login (in addition to '
            'password). Staff cannot disable this. Ignored if the user has an '
            'authenticator app (TOTP) configured — TOTP replaces the email '
            'code step.'
        ),
    )

    # ── Seller fields ────────────────────────────────────────────────────────
    # A seller is a User account with is_seller ticked; there is no separate
    # Seller record any more. Everything below is meaningless unless is_seller
    # is True, and is prefixed so it reads that way at every call site.
    #
    # Sellers do not create their own listings — an admin does, through Add
    # Listing — so these are admin-maintained details about the person whose
    # plants are being sold, not a self-service profile.
    is_seller = models.BooleanField(
        default=False,
        help_text=(
            'This account sells plants. Seller-flagged users can be chosen when '
            'adding a listing, appear on the public category and seller pages, '
            'and get a read-only sales dashboard.'
        ),
    )
    seller_bio = models.TextField(
        blank=True,
        help_text='Shown on this seller\'s public page.',
    )
    # max_digits=7 rather than the 6 originally sketched, to match the other two
    # shipping columns in this schema (AuctionListing.shipping_fee and
    # Invoice.shipping_fee) so a value can move between them unchanged.
    seller_shipping_fee = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text=(
            "This seller's standard shipping charge. Used for any of their "
            'listings that does not set its own.'
        ),
    )
    # ── How to pay this seller ───────────────────────────────────────────────
    # Free text, not URLs. Venmo, PayPal and Cash App have real payment links,
    # but Zelle has no link at all (just an email or phone), and Apple/Google
    # Pay are not visit-a-link methods either — so each field holds whatever
    # the seller wants shown: a handle, a link, an email, a phone number.
    venmo_info = models.CharField(max_length=255, blank=True, verbose_name='Venmo')
    paypal_info = models.CharField(max_length=255, blank=True, verbose_name='PayPal')
    cashapp_info = models.CharField(
        max_length=255, blank=True, verbose_name='Cash App'
    )
    apple_pay_info = models.CharField(
        max_length=255, blank=True, verbose_name='Apple Pay'
    )
    google_pay_info = models.CharField(
        max_length=255, blank=True, verbose_name='Google Pay'
    )
    zelle_info = models.CharField(max_length=255, blank=True, verbose_name='Zelle')
    seller_payment_methods = models.TextField(
        blank=True,
        verbose_name='Other payment notes',
        help_text=(
            'Anything the named fields above do not cover — a mailing address '
            'for cheques, "cash at pickup", or a note about how they prefer to '
            'be paid.'
        ),
    )
    seller_category = models.ForeignKey(
        AuctionCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sellers',
        help_text='The category this seller primarily lists under.',
    )
    seller_active_week = models.DateField(
        null=True, blank=True,
        help_text='Start date of the week this seller is active.',
    )
    seller_notify_on_comments = models.BooleanField(
        default=True,
        help_text='Email this seller when a buyer posts a question on their listing.',
    )

    # Field name -> the label a buyer sees. Ordered by how commonly these get
    # used for plant sales, since that is the order they are read in.
    PAYMENT_FIELDS = (
        ('paypal_info', 'PayPal'),
        ('venmo_info', 'Venmo'),
        ('zelle_info', 'Zelle'),
        ('cashapp_info', 'Cash App'),
        ('apple_pay_info', 'Apple Pay'),
        ('google_pay_info', 'Google Pay'),
    )

    @property
    def payment_options(self):
        """
        The seller's payment details as ``[(label, detail), ...]``.

        Only the ones actually filled in, so a seller who takes PayPal and
        nothing else does not advertise five blank methods. One source for the
        invoice page and the invoice email, which must not disagree about how
        to pay someone.
        """
        options = [
            (label, getattr(self, field).strip())
            for field, label in self.PAYMENT_FIELDS
            if getattr(self, field).strip()
        ]
        if self.seller_payment_methods.strip():
            options.append(('Other', self.seller_payment_methods.strip()))
        return options

    @property
    def payment_method_names(self):
        """
        Just the names of the methods accepted — no handles or addresses.

        What the public seller page shows. A buyer deciding whether to bid needs
        to know they can pay by Venmo; the handle itself is only useful once
        they owe money, so it stays on their own invoice.
        """
        return [
            label for field, label in self.PAYMENT_FIELDS
            if getattr(self, field).strip()
        ]

    @property
    def display_name(self) -> str:
        """
        What to call this person on screen.

        The old Seller model carried a free-text ``name``; a User does not, so
        this is the one place that decides how a name is assembled. Full name
        when we have one, otherwise the username — allauth derives that from the
        email address at signup, so it is at least recognisable.
        """
        return self.user.get_full_name().strip() or self.user.get_username()

    def active_listings(self):
        """
        This seller's listings a visitor can currently act on.

        Delegates to queries.active_listings so "currently for sale" has one
        definition — the public seller page, the seller's own dashboard and the
        category page's per-seller count all have to agree on it, and a future
        browse-everything page uses the same query with no seller.

        Imported here rather than at module scope because queries.py imports
        this module.
        """
        from .queries import active_listings

        return active_listings(seller=self.user)

    def __str__(self) -> str:
        return f'Profile – {self.user.username}'


class AuctionListing(models.Model):
    LISTING_TYPE_CHOICES = [
        ('auction', 'Auction'),
        ('buy_now', 'Buy It Now'),
    ]

    SHIPPING_MODE_CHOICES = [
        ('flat', 'Flat fee per order'),
        ('per_item', 'Multiplied by quantity'),
    ]

    title = models.CharField(max_length=255)
    category = models.ForeignKey(
        AuctionCategory,
        on_delete=models.PROTECT,
        related_name='listings',
    )
    # PROTECT and non-null: a listing with no seller has nobody to pay and no
    # shipping fee to quote, which the old nullable column made representable
    # and every reader then had to guard against.
    seller = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        limit_choices_to={'profile__is_seller': True},
        related_name='listings',
    )
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='listings/', blank=True)
    start_price = models.DecimalField(max_digits=9, decimal_places=2)
    current_bid = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    reserve_price = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True
    )
    listing_type = models.CharField(
        max_length=8, choices=LISTING_TYPE_CHOICES, default='auction'
    )
    buy_now_price = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True
    )
    # ── Buy It Now stock. Null on auction listings, which sell a single lot to
    # a single winner and use `winner` instead. ──────────────────────────────
    quantity_available = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Total units available for this Buy It Now listing.',
    )
    quantity_remaining = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            'Units still available for purchase. Set equal to '
            'quantity_available when the listing is created; decremented as '
            'purchases come in.'
        ),
    )
    additional_item_discount = models.DecimalField(
        max_digits=6, decimal_places=2, default=0, blank=True,
        help_text=(
            'Amount off each unit beyond the first, for Buy It Now listings. '
            'Set per listing by the admin — buy 3 of a $12 plant with a $3 '
            'discount and it costs $12 + $9 + $9. Leave at 0 for no discount.'
        ),
    )
    shipping_mode = models.CharField(
        max_length=10,
        choices=SHIPPING_MODE_CHOICES,
        default='flat',
        help_text=(
            'How shipping is calculated when a buyer takes more than one. '
            'Flat fee: the buyer pays shipping once no matter how many they '
            'buy. Per item: shipping is multiplied by the quantity they buy.'
        ),
    )
    shipping_fee = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text=(
            "Shipping cost for this listing. Leave blank to use the seller's "
            'standard shipping fee. Combined with the shipping mode above: a '
            'flat fee is charged once per order, a per-item fee is multiplied '
            'by the quantity bought.'
        ),
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    is_closed = models.BooleanField(default=False)
    winner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='won_listings',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.listing_type == 'buy_now':
            if self.buy_now_price is None or self.buy_now_price <= 0:
                raise ValidationError(
                    {
                        'buy_now_price':
                            'Buy It Now listings require a price greater than 0.'
                    }
                )
            if self.quantity_available is None or self.quantity_available < 1:
                raise ValidationError(
                    {
                        'quantity_available':
                            'Buy It Now listings require a quantity of at '
                            'least 1.'
                    }
                )
            # A discount above the price would make extra units free, and the
            # arithmetic floors at zero rather than paying the buyer — so an
            # over-large discount is silently capped instead of doing what was
            # typed. Refusing it here means the number on screen is the number
            # charged.
            discount = self.additional_item_discount or 0
            if discount < 0:
                raise ValidationError(
                    {'additional_item_discount': 'A discount cannot be negative.'}
                )
            if discount > self.buy_now_price:
                raise ValidationError(
                    {
                        'additional_item_discount':
                            f'The discount cannot exceed the price of '
                            f'${self.buy_now_price}.'
                    }
                )

    def save(self, *args, **kwargs):
        """
        Keep Buy It Now stock coherent.

        On creation, quantity_remaining mirrors quantity_available unless the
        caller set it explicitly — clean() cannot do this, since it does not run
        for ``objects.create()`` or for the bulk weekly-setup path.

        quantity_available defaults to 1 rather than staying null so that a
        buy_now listing created programmatically is purchasable instead of
        silently unbuyable. That also matches what a buy_now listing meant
        before stock existed: exactly one unit. Auction listings keep both
        fields null; they sell one lot via `winner`.
        """
        if self.listing_type == 'buy_now':
            if self.quantity_available is None:
                self.quantity_available = 1
            if self._state.adding and self.quantity_remaining is None:
                self.quantity_remaining = self.quantity_available

        super().save(*args, **kwargs)

    @property
    def has_completed_sale(self) -> bool:
        """
        Whether this listing actually sold something to somebody.

        Deliberately not "is it over": a listing that ran its course with no
        bids and no buyers has sold nothing, and an admin should be free to give
        it new dates and run it again rather than having to copy it first. What
        must not change underneath a sale is a listing somebody has committed to
        buy — its title and price are what the invoice says they agreed to.

        For an auction that means a winner; for Buy It Now, any invoice at all,
        since several buyers can each take a share and `winner` stays unset.
        """
        if self.listing_type == 'buy_now':
            # The admin changelist annotates _has_invoice so a page of rows
            # costs one query instead of one per row; fall back to asking.
            annotated = getattr(self, '_has_invoice', None)
            if annotated is not None:
                return bool(annotated)
            return self.invoices.exists()
        return self.winner_id is not None

    @property
    def units_remaining(self) -> int:
        """Units a buyer can still take. Always 0 for auction listings."""
        if self.listing_type != 'buy_now':
            return 0
        return self.quantity_remaining or 0

    @property
    def is_sold_out(self) -> bool:
        return self.listing_type == 'buy_now' and self.units_remaining == 0

    @property
    def additional_unit_price(self):
        """
        What each unit beyond the first costs.

        Floored at zero: the field validation refuses a discount larger than
        the price, but a listing saved before that check existed — or written
        by a bulk update, which does not run clean() — must not price a plant
        below nothing.
        """
        if self.buy_now_price is None:
            return Decimal('0.00')
        price = Decimal(str(self.buy_now_price))
        discount = Decimal(str(self.additional_item_discount or 0))
        return max(price - discount, Decimal('0.00'))

    @property
    def has_quantity_discount(self) -> bool:
        return bool(
            self.listing_type == 'buy_now' and self.additional_item_discount
        )

    def price_for(self, quantity: int):
        """
        The item total for ``quantity`` units, before shipping.

        Full price for the first, ``additional_unit_price`` for each after it.
        One definition, used by the purchase view, the listing page and the
        invoice emails — three places that must agree about what a buyer owes.
        """
        if self.buy_now_price is None or quantity < 1:
            return Decimal('0.00')
        first = Decimal(str(self.buy_now_price))
        return first + (quantity - 1) * self.additional_unit_price

    @property
    def shipping_rate(self):
        """
        The per-order (or per-item) shipping charge for this listing.

        The listing's own fee when one is set, otherwise the seller's standard
        fee. Plants do not all ship alike — a big double fan costs more to send
        than a small one — so a Buy It Now listing can carry its own price
        without disturbing the seller's default for everything else.
        """
        if self.shipping_fee is not None:
            base = self.shipping_fee
        else:
            profile = getattr(self.seller, 'profile', None)
            base = getattr(profile, 'seller_shipping_fee', None) or 0
        # Coerced because callers (and this project's own fixtures) sometimes
        # assign decimal fields as strings; '5.00' * 3 would silently produce
        # '5.005.005.00' rather than 15.00.
        return Decimal(str(base))

    def shipping_for(self, quantity: int):
        """
        Shipping charged for ``quantity`` units, per this listing's mode.

        'flat' bills the rate once however many units are bought; 'per_item'
        multiplies it. Auction listings never reach here.
        """
        base = self.shipping_rate
        if self.shipping_mode == 'per_item':
            return base * quantity
        return base

    def __str__(self) -> str:
        return self.title


class Bid(models.Model):
    listing = models.ForeignKey(
        AuctionListing,
        on_delete=models.CASCADE,
        related_name='bids',
    )
    bidder = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='bids',
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    placed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-placed_at']

    def __str__(self) -> str:
        return f'{self.bidder.username} – ${self.amount} on "{self.listing}"'


class ProxyBid(models.Model):
    listing = models.ForeignKey(
        AuctionListing,
        on_delete=models.CASCADE,
        related_name='proxy_bids',
    )
    bidder = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='proxy_bids',
    )
    max_amount = models.DecimalField(max_digits=9, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('listing', 'bidder')]

    def __str__(self) -> str:
        return f'{self.bidder.username} – max ${self.max_amount} on "{self.listing}"'


class CombinedInvoice(models.Model):
    """
    One bill covering everything a buyer owes one seller.

    A buyer who wins three plants from the same grower over a fortnight gets
    one invoice for all three, not three invoices — and pays shipping once.
    The individual Invoice rows become its line items.

    Nothing here is automatic except the grouping. An admin decides when to
    generate drafts, reviews each one (adjusting shipping or adding a
    discount), and clicks Send. Until that click the buyer has heard nothing
    at all: **this email is the notification** that they won or bought
    something, which is why nothing else emails them any more.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
    ]

    buyer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='combined_invoices',
    )
    seller = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        limit_choices_to={'profile__is_seller': True},
        related_name='combined_invoices_as_seller',
    )
    status = models.CharField(
        max_length=5, choices=STATUS_CHOICES, default='draft'
    )
    shipping_override = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text=(
            'If set, replaces the sum of the line items\' shipping fees. Use '
            'it when several plants ship in one box for less than the '
            'individual fees add up to.'
        ),
    )
    discount_amount = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text='Flat dollar discount applied to this invoice total.',
    )
    notes = models.TextField(
        blank=True,
        help_text='Shown to the buyer on the invoice email.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # The "open running tab": a buyer+seller pair has at most one
            # unsent invoice, so a second Generate run adds to the existing
            # draft rather than starting a rival one. Enforced here as well as
            # in the generation code, because two admins clicking Generate at
            # the same moment is exactly the case the code alone would miss.
            models.UniqueConstraint(
                fields=['buyer', 'seller'],
                condition=models.Q(status='draft'),
                name='one_open_draft_per_buyer_and_seller',
            ),
        ]

    # Money always reads as money. SUM() hands back a Decimal whose exponent
    # depends on the backend — SQLite returns Decimal('50') for 20.00 + 30.00 —
    # so an unquantized total renders as "$50" in an email and on the review
    # page. Every amount below goes through _money().
    _CENTS = Decimal('0.01')

    @staticmethod
    def _money(value):
        return (value or Decimal('0')).quantize(CombinedInvoice._CENTS)

    @property
    def subtotal(self):
        """Sum of the line items' amounts, before shipping and discount."""
        return self._money(
            self.line_items.aggregate(total=models.Sum('amount'))['total']
        )

    @property
    def summed_shipping(self):
        """What the line items' own shipping fees add up to."""
        return self._money(
            self.line_items.aggregate(total=models.Sum('shipping_fee'))['total']
        )

    @property
    def total_shipping(self):
        """The shipping actually charged — the override when one is set."""
        if self.shipping_override is not None:
            return self._money(self.shipping_override)
        return self.summed_shipping

    @property
    def total(self):
        return self._money(
            self.subtotal + self.total_shipping - self.discount_amount
        )

    @property
    def is_sent(self) -> bool:
        return self.status == 'sent'

    def __str__(self) -> str:
        return (
            f'Combined invoice #{self.pk} – '
            f'{self.buyer.username} / {self.seller.username} ({self.status})'
        )


class Invoice(models.Model):
    listing = models.ForeignKey(
        AuctionListing,
        on_delete=models.PROTECT,
        related_name='invoices',
        null=True,
        blank=True,
    )
    # Used when no linked listing exists (manual / off-platform entry)
    item_description = models.CharField(max_length=500, blank=True)
    buyer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    # related_name differs from buyer's 'invoices': both point at User now, so
    # user.invoices stays "what I bought" and does not silently become a mix.
    seller = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        limit_choices_to={'profile__is_seller': True},
        related_name='invoices_as_seller',
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text=(
            'Units this buyer purchased. Always 1 for auction wins; can be '
            'more for a multi-quantity Buy It Now listing, where one listing '
            'produces one invoice per buyer.'
        ),
    )
    # For buy_now this is buy_now_price * quantity, i.e. the line total before
    # shipping — not the per-unit price.
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=7, decimal_places=2)
    payment_method = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_sent = models.BooleanField(default=False)
    is_manually_created = models.BooleanField(default=False)
    # None means "not yet billed" — this is what drives the open running tab.
    # SET_NULL so deleting a draft returns its items to the unbilled pool
    # rather than destroying the record of a sale.
    combined_invoice = models.ForeignKey(
        CombinedInvoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='line_items',
        help_text=(
            'The combined invoice this line belongs to. Empty means it has '
            'not been billed to the buyer yet.'
        ),
    )

    class Meta:
        ordering = ['-created_at']

    @property
    def item_display(self):
        if self.listing:
            return self.listing.title
        return self.item_description or '—'

    @property
    def total(self):
        return self.amount + self.shipping_fee

    def __str__(self) -> str:
        return f'Invoice #{self.pk} – {self.buyer.username} / {self.listing}'


class ListingComment(models.Model):
    listing = models.ForeignKey(
        AuctionListing,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='listing_comments',
    )
    body = models.TextField()
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies',
    )
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def save(self, *args, **kwargs):
        # Replies inherit their parent's listing so admins only set the parent.
        if self.parent_id and not self.listing_id:
            self.listing = self.parent.listing
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'Comment by {self.author.username} on "{self.listing}"'


class Wishlist(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='wishlist_entries',
    )
    listing_title_keyword = models.CharField(max_length=200)
    notified = models.BooleanField(default=False)

    class Meta:
        ordering = ['user', 'listing_title_keyword']

    def __str__(self) -> str:
        return f'{self.user.username} – "{self.listing_title_keyword}"'


class FAQItem(models.Model):
    """One question and answer on the public FAQ page, ordered by the admin."""

    question = models.CharField(max_length=255)
    answer = models.TextField(
        help_text=(
            'Plain text is fine — line breaks are kept as you type them. HTML '
            'also works if you want a link.'
        ),
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text='Lower numbers appear first. Ties fall back to the question.',
    )
    is_published = models.BooleanField(
        default=True,
        help_text='Untick to keep this question off the public page.',
    )

    class Meta:
        # question as the tiebreak so a page of rows all left at 0 comes out in
        # a stable order rather than whatever the database happens to return.
        ordering = ['display_order', 'question']
        verbose_name = 'FAQ item'

    def __str__(self) -> str:
        return self.question


class SitePage(models.Model):
    """
    An admin-editable page of prose: Terms of Service, Privacy Policy, About Us.

    One model for all three rather than three models, because nothing about
    them differs except the words — they are looked up by slug and rendered by
    the same view. The rows are seeded by migration 0020; an admin edits them
    but should not normally need to create or delete any.
    """

    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text=(
            'Appears in the page URL, e.g. "terms-of-service" is served at '
            '/legal/terms-of-service/. Changing it changes the address, so '
            'existing links to this page will break.'
        ),
    )
    title = models.CharField(max_length=200)
    body = models.TextField(
        help_text=(
            'HTML is allowed, so headings (<h2>), lists (<ul><li>) and links '
            'work. Text with no markup keeps its line breaks.'
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def get_absolute_url(self):
        """
        Where this page is served.

        About Us has an address of its own; everything else lives under /legal/.
        One method so the admin's "view on site" link, the nav and any template
        all agree, including for a page an admin adds later.
        """
        from django.urls import reverse

        if self.slug == 'about-us':
            return reverse('about_us')
        return reverse('site_page', kwargs={'slug': self.slug})

    def __str__(self) -> str:
        return self.title


class Subscription(models.Model):
    PLAN_CHOICES = [
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('lapsed', 'Lapsed'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan = models.CharField(max_length=10, choices=PLAN_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    paypal_subscription_id = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )
    paypal_plan_id = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    grace_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Set to 3 days after current_period_end when a payment fails',
    )

    class Meta:
        ordering = ['-id']

    def __str__(self) -> str:
        return f'{self.user.username} – {self.get_plan_display()} ({self.status})'
