from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Seller
from .weekly_setup_forms import WeeklyListingFormSet, WeeklySellerForm


class StaffRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden('Staff access required.')
        return super().dispatch(request, *args, **kwargs)


from django.views import View


class WeeklySetupSellerView(StaffRequiredMixin, View):
    """Step 1 — choose category and create or select the week's seller."""
    template_name = 'weekly_setup/seller.html'

    def get(self, request):
        form = WeeklySellerForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = WeeklySellerForm(request.POST)
        if form.is_valid():
            seller = form.save()
            messages.success(request, f'Seller "{seller.name}" ready. Now add listings below.')
            return redirect('weekly_setup_listings', seller_pk=seller.pk)
        return render(request, self.template_name, {'form': form})


class WeeklySetupListingsView(StaffRequiredMixin, View):
    """Step 2 — bulk-upload listings for the chosen seller."""
    template_name = 'weekly_setup/listings.html'

    def _get_seller(self, seller_pk):
        return get_object_or_404(Seller.objects.select_related('category'), pk=seller_pk)

    def get(self, request, seller_pk):
        seller = self._get_seller(seller_pk)
        formset = WeeklyListingFormSet(prefix='listings')
        return render(request, self.template_name, {'seller': seller, 'formset': formset})

    def post(self, request, seller_pk):
        seller = self._get_seller(seller_pk)
        formset = WeeklyListingFormSet(request.POST, request.FILES, prefix='listings')

        if not formset.is_valid():
            return render(request, self.template_name, {'seller': seller, 'formset': formset})

        saved = skipped = 0
        for form in formset:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            if form.is_empty():
                continue
            if not form.has_required_data():
                skipped += 1
                continue

            listing = form.save(commit=False)
            listing.seller = seller
            listing.category = seller.category
            listing.current_bid = 0
            listing.is_active = True
            listing.is_closed = False

            # Ensure timezone-aware datetimes when USE_TZ is on.
            if listing.starts_at and timezone.is_naive(listing.starts_at):
                listing.starts_at = timezone.make_aware(listing.starts_at)
            if listing.ends_at and timezone.is_naive(listing.ends_at):
                listing.ends_at = timezone.make_aware(listing.ends_at)

            listing.save()
            saved += 1

        if skipped:
            messages.warning(
                request,
                f'{skipped} row(s) skipped — title, start price, and dates are all required.',
            )
        if saved:
            messages.success(request, f'{saved} listing(s) created for {seller.name}.')
            return redirect('weekly_setup_done', seller_pk=seller.pk)

        messages.error(request, 'No listings were saved. Please fill in at least one row completely.')
        return render(request, self.template_name, {'seller': seller, 'formset': formset})


class WeeklySetupDoneView(StaffRequiredMixin, View):
    template_name = 'weekly_setup/done.html'

    def get(self, request, seller_pk):
        seller = get_object_or_404(Seller.objects.select_related('category'), pk=seller_pk)
        listings = seller.listings.filter(is_closed=False).order_by('starts_at')
        return render(request, self.template_name, {'seller': seller, 'listings': listings})
