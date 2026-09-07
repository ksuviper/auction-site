from django import forms

from .models import CombinedInvoice


class CombinedInvoiceReviewForm(forms.ModelForm):
    """
    The three things an admin can change on a draft before sending it.

    Line items are not editable here — an item's amount is what a buyer
    committed to when they bid or bought, so correcting one is an Invoice-level
    fix, not part of billing.
    """

    class Meta:
        model = CombinedInvoice
        fields = ['shipping_override', 'discount_amount', 'notes']
        widgets = {
            'shipping_override': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg',
                'step': '0.01', 'min': '0',
            }),
            'discount_amount': forms.NumberInput(attrs={
                'class': 'form-control form-control-lg',
                'step': '0.01', 'min': '0',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control form-control-lg', 'rows': 3,
                'placeholder': 'Optional — shown to the buyer on their invoice.',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['shipping_override'].label = 'Shipping charged'
        self.fields['shipping_override'].help_text = (
            'Leave blank to charge the line items\' fees added up. Set it when '
            'several plants ship together for less.'
        )
        self.fields['discount_amount'].label = 'Discount'
        self.fields['discount_amount'].required = False

    def clean_discount_amount(self):
        """Blank means no discount; the model column is NOT NULL."""
        discount = self.cleaned_data.get('discount_amount')
        if discount in (None, ''):
            return 0
        if discount < 0:
            raise forms.ValidationError('A discount cannot be negative.')
        return discount

    def clean_shipping_override(self):
        override = self.cleaned_data.get('shipping_override')
        if override is not None and override < 0:
            raise forms.ValidationError('Shipping cannot be negative.')
        return override

    def clean(self):
        """
        A discount cannot exceed the bill.

        Cross-field, so it belongs here rather than in clean_discount_amount:
        the shipping being charged is part of the ceiling, and a ModelForm only
        copies submitted values onto the instance after every field cleaner has
        run — so reading self.instance there would compare against the *old*
        shipping override.

        Without this an admin could type an extra zero and email the buyer a
        negative total, which reads as the club owing them money.
        """
        cleaned = super().clean()
        discount = cleaned.get('discount_amount') or 0

        override = cleaned.get('shipping_override')
        shipping = (
            override if override is not None else self.instance.summed_shipping
        )
        billable = self.instance.subtotal + shipping

        if discount > billable:
            self.add_error(
                'discount_amount',
                f'That is more than the invoice total of ${billable}. A '
                'discount cannot make the total negative.',
            )
        return cleaned
