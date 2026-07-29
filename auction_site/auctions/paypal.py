"""
Lightweight PayPal REST client for the Subscriptions API.

The classic ``paypalrestsdk`` does not support PayPal's modern
``/v1/billing`` (Subscriptions) API, so this module makes authenticated REST
calls directly via ``requests``. ``paypalrestsdk`` is still used elsewhere for
webhook signature verification.

All calls respect ``PAYPAL_MODE`` ('sandbox' vs 'live') from settings.
Reference: https://developer.paypal.com/docs/subscriptions/
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SANDBOX_BASE = 'https://api-m.sandbox.paypal.com'
LIVE_BASE = 'https://api-m.paypal.com'

_TIMEOUT = 30


class PayPalError(Exception):
    """Raised when a PayPal API call fails."""


def get_base_url() -> str:
    """Return the PayPal API base URL for the configured mode."""
    mode = (settings.PAYPAL_MODE or 'sandbox').lower()
    return LIVE_BASE if mode == 'live' else SANDBOX_BASE


def get_access_token() -> str:
    """Obtain an OAuth2 access token using the client credentials grant."""
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise PayPalError('PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not configured.')

    resp = requests.post(
        f'{get_base_url()}/v1/oauth2/token',
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
        data={'grant_type': 'client_credentials'},
        headers={'Accept': 'application/json'},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise PayPalError(
            f'Failed to obtain access token ({resp.status_code}): {resp.text}'
        )
    return resp.json()['access_token']


def paypal_request(method, path, token=None, json=None, headers=None):
    """
    Make an authenticated PayPal REST request and return the ``requests``
    response. Callers are responsible for checking ``resp.status_code``.

    ``path`` may be an absolute URL or a path relative to the API base.
    A fresh access token is fetched if one is not supplied.
    """
    if token is None:
        token = get_access_token()

    request_headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    if headers:
        request_headers.update(headers)

    url = path if path.startswith('http') else f'{get_base_url()}{path}'
    return requests.request(
        method, url, json=json, headers=request_headers, timeout=_TIMEOUT
    )
