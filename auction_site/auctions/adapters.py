from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse


class AccountAdapter(DefaultAccountAdapter):
    """Redirect new sign-ups to profile completion before landing on home."""

    def get_signup_redirect_url(self, request):
        """
        Landing page for a completed sign-up.

        With ACCOUNT_EMAIL_VERIFICATION = 'mandatory' the sign-up does not
        finish until the confirmation link is clicked, so this is also where
        allauth sends a user who has just confirmed their address:
        ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION resumes the stashed sign-up login,
        and allauth's get_login_redirect_url() honours this method (rather than
        LOGIN_REDIRECT_URL) whenever the login it is resuming came from a
        sign-up. Confirming in a different browser than the one that signed up
        leaves the session behind, so that user lands on the login page instead
        — the allauth default, and the only sensible page for an anonymous
        visitor.
        """
        return reverse('profile_edit') + '?next=/'
