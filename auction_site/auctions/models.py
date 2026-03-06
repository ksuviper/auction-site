from django.db import models


class AuctionCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Auction categories'

    def __str__(self) -> str:
        return self.name


class AuctionListing(models.Model):
    title = models.CharField(max_length=255)
    category = models.ForeignKey(
        AuctionCategory,
        on_delete=models.PROTECT,
        related_name='listings',
    )
    description = models.TextField(blank=True)
    start_price = models.DecimalField(max_digits=9, decimal_places=2)
    current_bid = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.title
