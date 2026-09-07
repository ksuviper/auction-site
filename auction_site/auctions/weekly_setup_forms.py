from django import forms
from django.contrib.auth import get_user_model

from .models import AuctionCategory, AuctionListing

User = get_user_model()


def seller_queryset():
    """
    The accounts that may be chosen as a seller, in a stable display order.

    A seller is a User with profile.is_seller ticked — there is no separate
    Seller record, and nothing here creates an account. Someone who should be
    selectable but is not gets flagged in the admin first.
    """
    return (
        User.objects.filter(profile__is_seller=True)
        .select_related('profile')
        .order_by('first_name', 'last_name', 'username')
    )


class SellerChoiceField(forms.ModelChoiceField):
    """A seller picker labelled by display name rather than by username."""

    def label_from_instance(self, obj):
        profile = getattr(obj, 'profile', None)
        name = profile.display_name if profile else obj.get_username()
        week = getattr(profile, 'seller_active_week', None)
        return f'{name} (week of {week})' if week else name


class WeeklySellerForm(forms.Form):
    """
    Step 1 — choose which seller this week's listings belong to.

    Selection only. Creating a seller inline used to happen here, which meant
    the same person could end up as both a User account and one or more
    standalone Seller rows; a seller is now just a User with the seller flag
    ticked, set in the admin.
    """

    seller = SellerChoiceField(
        queryset=User.objects.none(),
        empty_label='— select a seller —',
        label='Seller',
        help_text='Only accounts flagged as sellers in the admin appear here.',
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Evaluated per instance so a seller flagged mid-session shows up on the
        # next page load rather than at process start.
        self.fields['seller'].queryset = seller_queryset()

    def save(self):
        return self.cleaned_data['seller']


class AuctionListingForm(forms.ModelForm):
    """Create one listing at a time for a given seller.

    Replaces the old six-row formset. Everything a listing needs is asked for
    here, including category — the previous flow collected no category and the
    view set none, so saving raised NOT NULL on auctions_auctionlisting.category_id
    and no listing was ever created through weekly setup.
    """

    class Meta:
        model = AuctionListing
        fields = [
            'title', 'description', 'image', 'category', 'listing_type',
            'start_price', 'reserve_price',
            'buy_now_price', 'quantity_available',
            'additional_item_discount', 'shipping_mode', 'shipping_fee',
            'starts_at', 'ends_at',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'e.g. Stella de Oro',
                'autofocus': 'autofocus',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control form-control-lg', 'rows': 4,
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-lg',
                'accept': 'image/*',
                'id': 'id_image',
            }),
            'category': forms.Select(attrs={'class': 'form-select form-select-lg'}),
            'listing_type': forms.Select(attrs={
                'class': 'form-select form-select-lg listing-type-select',
            }),
            'start_price': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'step': '0.01', 'min': '0',
            }),
            'reserve_price': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'step': '0.01', 'min': '0',
            }),
            'buy_now_price': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'step': '0.01', 'min': '0',
            }),
            'quantity_available': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'min': 1, 'step': 1,
            }),
            'additional_item_discount': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'step': '0.01', 'min': '0',
            }),
            'shipping_mode': forms.Select(attrs={'class': 'form-select form-select-lg'}),
            'shipping_fee': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg', 'step': '0.01', 'min': '0',
            }),
            'starts_at': forms.DateTimeInput(
                attrs={'class': 'form-control form-control-lg', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'ends_at': forms.DateTimeInput(
                attrs={'class': 'form-control form-control-lg', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
        }

    def __init__(self, *args, seller=None, **kwargs):
        self.seller = seller
        super().__init__(*args, **kwargs)

        self.fields['category'].queryset = AuctionCategory.objects.filter(
            is_active=True
        )
        self.fields['category'].empty_label = '— choose a category —'

        for name in ('starts_at', 'ends_at'):
            self.fields[name].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']

        # Required on every listing; the type-specific prices are checked in
        # clean(), since which of them is needed depends on listing_type.
        for name in ('title', 'category', 'listing_type', 'starts_at', 'ends_at'):
            self.fields[name].required = True
        # shipping_mode has a model default but no blank=True, so the ModelForm
        # would demand it on every listing — including auctions, which never
        # show the field. clean() falls back to the model default.
        for name in ('start_price', 'buy_now_price', 'quantity_available',
                     'additional_item_discount', 'shipping_mode',
                     'shipping_fee'):
            self.fields[name].required = False

        # PositiveIntegerField contributes min_value=0, which overrides the
        # widget attr from Meta; a listing of zero units is not a listing.
        self.fields['quantity_available'].widget.attrs['min'] = 1

        self.fields['start_price'].help_text = 'Opening bid for an auction.'
        self.fields['reserve_price'].help_text = (
            'Optional. The listing will not sell below this.'
        )
        self.fields['quantity_available'].help_text = 'How many units are for sale.'
        self.fields['additional_item_discount'].label = 'Multi-buy discount'
        self.fields['additional_item_discount'].help_text = (
            'Optional. Taken off each plant after the first, so a $12 plant '
            'with a $3 discount costs $12 + $9 + $9 for three. Leave blank for '
            'no discount.'
        )
        self.fields['shipping_mode'].help_text = (
            'Flat fee: buyer pays shipping once no matter how many they buy. '
            'Per item: shipping is multiplied by the quantity they buy.'
        )
        seller_profile = getattr(self.seller, 'profile', None)
        standard_fee = getattr(seller_profile, 'seller_shipping_fee', None)
        if seller_profile is not None and standard_fee is not None:
            self.fields['shipping_fee'].help_text = (
                f'What the buyer pays to ship this plant. Leave blank to use '
                f"{seller_profile.display_name}'s standard fee of "
                f'${standard_fee}.'
            )
        else:
            self.fields['shipping_fee'].help_text = (
                'What the buyer pays to ship this plant. This seller has no '
                'standard fee on file, so leaving it blank means free shipping.'
            )

    def clean(self):
        cleaned = super().clean()
        listing_type = cleaned.get('listing_type')

        if listing_type == 'buy_now':
            if not cleaned.get('buy_now_price'):
                self.add_error('buy_now_price', 'Required for a Buy It Now listing.')
            if not cleaned.get('quantity_available'):
                self.add_error(
                    'quantity_available', 'Enter how many units are available.'
                )
            # start_price is NOT NULL on the model but means nothing for a fixed
            # price sale, so it is not asked for. Mirror the Buy It Now price so
            # anything reading it sees the real price rather than 0.
            if cleaned.get('buy_now_price') and not cleaned.get('start_price'):
                cleaned['start_price'] = cleaned['buy_now_price']
            if not cleaned.get('shipping_mode'):
                cleaned['shipping_mode'] = 'flat'
            # The column is NOT NULL with a default of 0; a blank box means
            # "no discount", not "leave it unset".
            if cleaned.get('additional_item_discount') in (None, ''):
                cleaned['additional_item_discount'] = 0
            price = cleaned.get('buy_now_price')
            discount = cleaned.get('additional_item_discount')
            if price and discount and discount > price:
                self.add_error(
                    'additional_item_discount',
                    f'The discount cannot be more than the price of ${price}.',
                )
        else:
            if not cleaned.get('start_price'):
                self.add_error('start_price', 'Required for an auction listing.')
            # Buy It Now inputs may carry stale values from a switched type.
            cleaned['buy_now_price'] = None
            cleaned['quantity_available'] = None
            cleaned['additional_item_discount'] = 0
            cleaned['shipping_mode'] = 'flat'
            # Auction shipping is settled from the seller's fee when the auction
            # closes, so a per-listing amount would be collected and never used.
            cleaned['shipping_fee'] = None

        starts_at, ends_at = cleaned.get('starts_at'), cleaned.get('ends_at')
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error('ends_at', 'The end time must be after the start time.')

        return cleaned
