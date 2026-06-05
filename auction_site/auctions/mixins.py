"""Shared view mixins."""

from django.http import HttpResponseForbidden


class StaffRequiredMixin:
    """Require an authenticated staff user; return 403 otherwise."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden('Staff access required.')
        return super().dispatch(request, *args, **kwargs)
