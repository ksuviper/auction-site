from django.contrib.auth import get_user_model
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

    def __str__(self) -> str:
        return f'Profile – {self.user.username}'


class Seller(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(
        blank=True,
        help_text='Email address for auction-end notifications',
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
