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

    def __str__(self) -> str:
        return f'Profile – {self.user.username}'


class Seller(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(
        blank=True,
        help_text='Email address for auction-end notifications',
    )
    notify_on_comments = models.BooleanField(
        default=True,
        help_text='Email this seller when a buyer posts a question on their listing.',
    )
    bio = models.TextField(blank=True)
    accepted_payment_methods = models.TextField(
        help_text='e.g. PayPal, Venmo, Zelle'
    )
    shipping_fee = models.DecimalField(max_digits=7, decimal_places=2)
    active_week = models.DateField(
        null=True, blank=True,
        help_text='Start date of the week this seller is active',
    )

    class Meta:
        ordering = ['-active_week', 'name']

    def __str__(self) -> str:
        return f'{self.name} ({self.active_week})'


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
    seller = models.ForeignKey(
        Seller,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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
    def units_remaining(self) -> int:
        """Units a buyer can still take. Always 0 for auction listings."""
        if self.listing_type != 'buy_now':
            return 0
        return self.quantity_remaining or 0

    @property
    def is_sold_out(self) -> bool:
        return self.listing_type == 'buy_now' and self.units_remaining == 0

    def shipping_for(self, quantity: int):
        """
        Shipping charged for ``quantity`` units, per this listing's mode.

        'flat' bills the seller's fee once however many units are bought;
        'per_item' multiplies it. Auction listings never reach here.
        """
        base = self.seller.shipping_fee if self.seller else 0
        # Coerced because callers (and this project's own fixtures) sometimes
        # assign decimal fields as strings; '5.00' * 3 would silently produce
        # '5.005.005.00' rather than 15.00.
        base = Decimal(str(base))
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
    seller = models.ForeignKey(
        Seller,
        on_delete=models.PROTECT,
        related_name='invoices',
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
