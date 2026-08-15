from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .mixins import StaffRequiredMixin
from .models import Seller
from .weekly_setup_forms import AuctionListingForm, WeeklySellerForm


class WeeklySetupSellerView(StaffRequiredMixin, View):
    """Step 1 — create or select the week's seller."""
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


class WeeklyListingCreateView(StaffRequiredMixin, View):
    """
    Step 2 — add listings for the chosen seller, one at a time.

    Replaces a six-row bulk formset. A successful save comes straight back here
    with an empty form rather than moving on, so an admin can enter a run of
    listings without navigating between each one. Everything already entered for
    this seller is listed underneath for reassurance, read-only.
    """

    template_name = 'weekly_setup/add_listing.html'

    def _get_seller(self, seller_pk):
        return get_object_or_404(Seller, pk=seller_pk)

    def _listings(self, seller):
        """This seller's listings, newest first — the admin's own entries on top."""
        return (
            seller.listings.select_related('category')
            .order_by('-created_at')
        )

    def _initial(self, seller):
        """
        Carry the previous listing's category and dates into the next one.

        A week's listings almost always share a category and a start/end window,
        so repeating them by hand for every entry is the tax that made the bulk
        grid feel necessary. Title, description, image and price stay blank —
        those are per-plant.

        Shipping starts at the seller's standard fee so the box always shows
        what the buyer would actually pay; overriding it for a plant that ships
        differently is then a deliberate edit rather than a blank to remember.
        """
        initial = {'shipping_fee': seller.shipping_fee}

        previous = self._listings(seller).first()
        if previous is not None:
            initial.update({
                'category': previous.category_id,
                'starts_at': previous.starts_at,
                'ends_at': previous.ends_at,
            })
        return initial

    def _context(self, seller, form):
        return {
            'seller': seller,
            'form': form,
            'listings': self._listings(seller),
        }

    def get(self, request, seller_pk):
        seller = self._get_seller(seller_pk)
        form = AuctionListingForm(seller=seller, initial=self._initial(seller))
        return render(request, self.template_name, self._context(seller, form))

    def post(self, request, seller_pk):
        seller = self._get_seller(seller_pk)
        form = AuctionListingForm(request.POST, request.FILES, seller=seller)

        if not form.is_valid():
            return render(request, self.template_name, self._context(seller, form))

        listing = form.save(commit=False)
        listing.seller = seller
        listing.current_bid = 0
        listing.is_active = True
        listing.is_closed = False
        # Model.save() mirrors quantity_remaining from quantity_available for
        # Buy It Now listings.
        listing.save()

        messages.success(
            request,
            f'Listing "{listing.title}" added. Add another below, or view all '
            f'listings for this seller.',
        )
        # Back to this same page, not onward to the listing: the next entry is
        # almost always the point.
        return redirect('weekly_setup_listings', seller_pk=seller.pk)


class WeeklySetupDoneView(StaffRequiredMixin, View):
    template_name = 'weekly_setup/done.html'

    def get(self, request, seller_pk):
        seller = get_object_or_404(Seller, pk=seller_pk)
        listings = seller.listings.filter(is_closed=False).order_by('starts_at')
        return render(request, self.template_name, {'seller': seller, 'listings': listings})
