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
