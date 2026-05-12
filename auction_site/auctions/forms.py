from decimal import Decimal

from django import forms

from .models import UserProfile


class CustomSignupForm(forms.Form):
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

    class Meta:
        model = UserProfile
        fields = ['phone_number', 'address']
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
