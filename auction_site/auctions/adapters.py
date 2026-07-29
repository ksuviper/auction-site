from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import has_verified_email
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .utils import needs_admin_approval

PENDING_APPROVAL_MESSAGE = (
    "Your account is awaiting admin approval. We'll email you as soon as "
    "it's approved."
)


class AccountAdapter(DefaultAccountAdapter):
    """Sign-up redirects plus the manual admin-approval gate on login."""

    def get_signup_redirect_url(self, request):
        """
        Landing page for a completed sign-up.

        Only reachable for accounts exempt from the approval gate (staff), since
        everyone else is held at the pending-approval page until an admin lets
        them in — see pre_login() below.
        """
        return reverse('profile_edit') + '?next=/'

    def get_email_verification_redirect_url(self, email_address):
        """
        Where to send someone after they click the email confirmation link.

        A new account has just done everything it can do on its own — the last
        step is out of their hands, so say so rather than dropping them on a
        login form that will only bounce them. The admin is notified of the
        pending account by the email_confirmed receiver in signals.py at the
        same moment.
        """
        if needs_admin_approval(email_address.user):
            return reverse('account_pending_approval')
        return super().get_email_verification_redirect_url(email_address)

    def pre_login(self, request, user, **kwargs):
        """
        Block un-approved accounts before a session is ever established.

        allauth calls this from perform_login() ahead of the login stages;
        returning a response here aborts the login, so nothing is written to the
        session. Every login path goes through it — email/password *and* social —
        which is what makes this the right hook for the gate.

        The super() call is not optional: DefaultAccountAdapter.pre_login is what
        rejects deactivated users (is_active=False), and skipping it would let
        them back in.
        """
        response = super().pre_login(request, user, **kwargs)
        if response:
            return response

        if kwargs.get('signup') and not has_verified_email(user, kwargs.get('email')):
            # The login attempt that sign-up itself makes. Mandatory email
            # verification is about to interrupt it anyway, and "check your
            # email" is the step the user can actually act on — telling them to
            # wait for approval first would be both premature and confusing.
            return None

        if needs_admin_approval(user):
            messages.info(request, PENDING_APPROVAL_MESSAGE)
            return redirect('account_pending_approval')

        return None
