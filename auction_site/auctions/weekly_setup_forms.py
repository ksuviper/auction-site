from django import forms
from django.forms import formset_factory

from .models import AuctionCategory, AuctionListing, Seller


class WeeklySellerForm(forms.Form):
    """Step 1 — pick a category and create or select the week's seller."""

    SELLER_MODE_CHOICES = [
        ('new', 'Create a new seller'),
        ('existing', 'Use an existing seller'),
    ]

    seller_mode = forms.ChoiceField(
        choices=SELLER_MODE_CHOICES,
        widget=forms.RadioSelect,
        initial='new',
    )
    existing_seller = forms.ModelChoiceField(
        queryset=Seller.objects.order_by('-active_week', 'name'),
        required=False,
        empty_label='— select —',
        help_text='Only required when using an existing seller.',
    )

    # Fields for creating / reviewing a seller
    category = forms.ModelChoiceField(
        queryset=AuctionCategory.objects.filter(is_active=True),
        required=False,
    )
    name = forms.CharField(max_length=200, required=False)
    email = forms.EmailField(required=False, help_text='Optional — for auction-end notifications.')
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)
    accepted_payment_methods = forms.CharField(
        required=False,
        help_text='e.g. PayPal, Venmo, Zelle',
    )
    shipping_fee = forms.DecimalField(max_digits=7, decimal_places=2, required=False)
    active_week = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Start date of the week this seller is active.',
    )

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('seller_mode')

        if mode == 'existing':
            if not cleaned.get('existing_seller'):
                self.add_error('existing_seller', 'Please select a seller.')
        else:
            required = {
                'category': 'Category',
                'name': 'Name',
                'accepted_payment_methods': 'Accepted payment methods',
                'shipping_fee': 'Shipping fee',
                'active_week': 'Active week',
            }
            for field, label in required.items():
                if not cleaned.get(field):
                    self.add_error(field, f'{label} is required when creating a new seller.')

        return cleaned

    def save(self):
        if self.cleaned_data['seller_mode'] == 'existing':
            return self.cleaned_data['existing_seller']
        return Seller.objects.create(
            category=self.cleaned_data['category'],
            name=self.cleaned_data['name'],
            email=self.cleaned_data.get('email') or '',
            bio=self.cleaned_data.get('bio') or '',
            accepted_payment_methods=self.cleaned_data['accepted_payment_methods'],
            shipping_fee=self.cleaned_data['shipping_fee'],
            active_week=self.cleaned_data['active_week'],
        )


class WeeklyListingForm(forms.ModelForm):
    """One row in the bulk-listing formset."""

    class Meta:
        model = AuctionListing
        fields = ['title', 'description', 'start_price', 'reserve_price', 'starts_at', 'ends_at', 'image']
        widgets = {
            'starts_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'ends_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'description': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # All fields optional at the Django level — we skip blank rows in the view.
        for field in self.fields.values():
            field.required = False
        self.fields['starts_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']
        self.fields['ends_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']

    def is_empty(self):
        return not self.cleaned_data.get('title')

    def has_required_data(self):
        d = self.cleaned_data
        return bool(d.get('title') and d.get('start_price') and d.get('starts_at') and d.get('ends_at'))


WeeklyListingFormSet = formset_factory(WeeklyListingForm, extra=6, can_delete=True)
