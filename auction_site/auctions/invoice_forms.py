from django import forms
from django.contrib.auth import get_user_model

from .models import AuctionListing, Invoice, Seller

User = get_user_model()


class InvoiceEditForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            'payment_method',
            'shipping_fee',
            'amount',
            'notes',
            'is_sent',
        ]
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'payment_method': forms.TextInput(attrs={'placeholder': 'e.g. PayPal, Venmo, Zelle'}),
        }


class InvoiceCreateForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            'listing',
            'item_description',
            'buyer',
            'seller',
            'amount',
            'shipping_fee',
            'payment_method',
            'notes',
            'is_sent',
        ]
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'item_description': forms.TextInput(attrs={
                'placeholder': 'Required when no listing is selected',
            }),
            'payment_method': forms.TextInput(attrs={'placeholder': 'e.g. PayPal, Venmo, Zelle'}),
        }

    def clean(self):
        cleaned = super().clean()
        listing = cleaned.get('listing')
        item_description = cleaned.get('item_description', '').strip()
        if not listing and not item_description:
            raise forms.ValidationError(
                'Provide either a listing or an item description.'
            )
        return cleaned
