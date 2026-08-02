"""Shared helpers for tests that need to actually get a user signed in.

Since every password login also requires an emailed one-time code, "log in" is
two requests. Tests about the other gates (admin approval, email verification)
should not each re-implement that, and should not sidestep it either — the point
of asserting "this user can log in" is that they reach an authenticated session.
"""

from django.core import mail

LOGIN_URL = '/accounts/login/'
LOGIN_CODE_URL = '/accounts/login/code/confirm/'


def extract_login_code():
    """
    Return the one-time code from the most recent login-code email.

    allauth's codes are uppercase alphanumeric (e.g. 'KQWFXP'), not digits, and
    sit on a line of their own.
    """
    for message in reversed(mail.outbox):
        if 'login code' not in message.subject.lower():
            continue
        for line in message.body.splitlines():
            token = line.strip()
            if len(token) >= 6 and token.isalnum() and token.upper() == token:
                return token
    raise AssertionError(
        'No login code email found. Subjects seen: '
        f'{[m.subject for m in mail.outbox]}'
    )


def sign_in(client, email, password, ip='198.51.100.99'):
    """
    Complete a password login, including the emailed code step when required.

    Returns the final response. If the login is stopped by something other than
    the code step — the admin-approval gate, an unverified email — that response
    is returned as-is, so callers can assert on it.
    """
    response = client.post(
        LOGIN_URL,
        {'login': email, 'password': password},
        REMOTE_ADDR=ip,
        follow=True,
    )

    if response.request['PATH_INFO'] != LOGIN_CODE_URL:
        return response

    return client.post(
        LOGIN_CODE_URL,
        {'code': extract_login_code()},
        REMOTE_ADDR=ip,
        follow=True,
    )


def is_signed_in(client):
    return client.session.get('_auth_user_id') is not None
