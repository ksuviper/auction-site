from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse


class AccountAdapter(DefaultAccountAdapter):
    """Redirect new sign-ups to profile completion before landing on home."""

    def get_signup_redirect_url(self, request):
        return reverse('profile_edit') + '?next=/'
