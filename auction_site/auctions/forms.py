from decimal import Decimal

from django import forms

from .models import UserProfile
from .utils import client_ip, verify_turnstile


class CustomSignupForm(forms.Form):
    """
    Extra fields mixed into allauth's signup forms via ACCOUNT_SIGNUP_FORM_CLASS.

    allauth makes this class a base of *both* the email/password signup form and
    the social signup form, so the Turnstile check below is opt-in: only
    ``RateLimitedSignupView`` passes ``require_turnstile=True``. Social sign-ups
    stay Turnstile-free — the provider does its own bot filtering.
    """

    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'First name',
            'autocomplete': 'given-name',
        }),
        label='First Name',
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Last name',
            'autocomplete': 'family-name',
        }),
        label='Last Name',
    )

    # Populated by the Turnstile widget's JS callback. Not required at the field
    # level — an empty token is rejected in clean() with a single friendly
    # message instead of Django's generic "This field is required".
    turnstile_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        # A plain Form has no access to the request, so the signup view hands it
        # over; verify_turnstile needs it for the caller's IP.
        self.request = kwargs.pop('request', None)
        self.require_turnstile = kwargs.pop('require_turnstile', False)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        if self.require_turnstile:
            token = cleaned_data.get('turnstile_token')
            if not verify_turnstile(token, client_ip(self.request)):
                raise forms.ValidationError(
                    'Please complete the verification check and try again.'
                )

        return cleaned_data

    def signup(self, request, user):
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save()


class ProfileUpdateForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'First name',
            'autocomplete': 'given-name',
        }),
        label='First Name',
    )
    last_name = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Last name',
            'autocomplete': 'family-name',
        }),
        label='Last Name',
    )

    COUNTRY_CHOICES = [
        ('', '— Select country —'),
        ('US', 'United States'),
        ('CA', 'Canada'),
        ('AU', 'Australia'),
        ('GB', 'United Kingdom'),
        ('NZ', 'New Zealand'),
        ('DE', 'Germany'),
        ('FR', 'France'),
        ('JP', 'Japan'),
        ('OTHER', 'Other'),
    ]

    country = forms.ChoiceField(
        choices=COUNTRY_CHOICES,
        required=False,
        label='Country',
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'}),
    )

    class Meta:
        model = UserProfile
        fields = ['phone_number', 'address', 'country']
        widgets = {
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'e.g. 555-867-5309',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control form-control-lg',
                'rows': 3,
                'placeholder': 'Street, City, State, ZIP',
            }),
        }
        labels = {
            'phone_number': 'Phone Number',
            'address': 'Mailing Address',
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number', '').strip()
        digits = ''.join(c for c in value if c.isdigit())
        if value and len(digits) < 7:
            raise forms.ValidationError('Please enter a valid phone number.')
        return value

    def save_user(self, user):
        user.first_name = self.cleaned_data.get('first_name', '')
        user.last_name = self.cleaned_data.get('last_name', '')
        user.save(update_fields=['first_name', 'last_name'])


class BidForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=9,
        decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': '0.00',
            'step': '0.01',
            'inputmode': 'decimal',
            'aria-label': 'Bid amount in dollars',
        }),
        label='Bid Amount',
        error_messages={
            'invalid': 'Please enter a valid dollar amount.',
            'min_value': 'Bid must be greater than zero.',
        },
    )


class BuyNowForm(forms.Form):
    """
    How many units of a Buy It Now listing the buyer wants.

    Validating against the listing here gives the buyer a friendly error, but it
    is not the safeguard that keeps stock from going negative — two buyers can
    both pass this check and then race. BuyNowView re-checks under
    select_for_update(); that is the real guard.
    """

    # required=False so a POST that omits the field still buys one, which is what
    # this endpoint did before quantities existed. A supplied value is still
    # validated, so 0 and negatives are rejected rather than defaulted.
    quantity = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg',
            'min': '1',
            'step': '1',
            'inputmode': 'numeric',
            'aria-label': 'How many to buy',
        }),
        label='Quantity',
        error_messages={
            'invalid': 'Please enter a whole number.',
            'min_value': 'Please buy at least one.',
        },
    )

    def __init__(self, *args, **kwargs):
        self.listing = kwargs.pop('listing')
        super().__init__(*args, **kwargs)
        remaining = self.listing.units_remaining
        if remaining:
            self.fields['quantity'].max_value = remaining
            self.fields['quantity'].widget.attrs['max'] = remaining

    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity') or 1
        remaining = self.listing.units_remaining

        if remaining <= 0:
            raise forms.ValidationError(
                'Sorry, this item has just sold out.'
            )
        if quantity > remaining:
            raise forms.ValidationError(
                f'Only {remaining} left — please lower the quantity.'
            )
        return quantity


class CommentForm(forms.Form):
    body = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={
            'class': 'form-control form-control-lg',
            'rows': 3,
            'placeholder': 'Ask a question or leave a comment…',
        }),
        label='Your question or comment',
    )


class ProxyBidForm(forms.Form):
    max_amount = forms.DecimalField(
        max_digits=9,
        decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': '0.00',
            'step': '0.01',
            'inputmode': 'decimal',
            'aria-label': 'Maximum bid amount in dollars',
        }),
        label="Maximum bid (we'll bid for you up to this amount)",
        error_messages={
            'invalid': 'Please enter a valid dollar amount.',
            'min_value': 'Maximum bid must be greater than zero.',
        },
    )
