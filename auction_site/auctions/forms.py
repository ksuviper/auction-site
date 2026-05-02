from decimal import Decimal

from django import forms

from .models import UserProfile


class ProfileUpdateForm(forms.ModelForm):
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

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number', '').strip()
        digits = ''.join(c for c in value if c.isdigit())
        if value and len(digits) < 7:
            raise forms.ValidationError('Please enter a valid phone number.')
        return value


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
