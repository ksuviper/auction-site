"""
Notification settings: push on this device, and the sellers a member follows.

Email is untouched by everything here. Turning push on adds a faster channel
for the same events; turning it off, or never turning it on, leaves a member
receiving exactly the emails they always did.
"""

import base64
import binascii
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from django_ratelimit.decorators import ratelimit

from .models import PushSubscription, SellerFollow
from .utils import is_allowed_push_endpoint, push_is_configured, seller_display_name

logger = logging.getLogger(__name__)

User = get_user_model()

# Devices one account may have push turned on for at once. Generous for a
# phone, a tablet and a couple of computers; the cap exists because every
# active row costs a network call on every notification, and endpoints are
# cheap to invent.
MAX_DEVICES_PER_USER = 10

# The sizes the Web Push spec fixes for a subscription's keys: an uncompressed
# P-256 public key and a 16-byte authentication secret.
P256DH_BYTES = 65
AUTH_BYTES = 16


def _decode_key(value, expected_bytes):
    """The bytes ``value`` encodes, if it is base64url for exactly that many."""
    if not isinstance(value, str) or not value or len(value) > 255:
        return None
    padded = value + '=' * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode('ascii'))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    return raw if len(raw) == expected_bytes else None


def _valid_p256dh(value):
    """
    True if ``value`` is a real point on the P-256 curve.

    Checked here rather than discovered later: a key of the right length that
    is not on the curve cannot be encrypted to, so it would be stored, fail on
    every notification, and never be switched off — the push service is never
    reached to say the subscription is gone.
    """
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = _decode_key(value, P256DH_BYTES)
    if raw is None:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError:
        return False
    return True


def describe_user_agent(user_agent):
    """
    "Chrome on Android" and the like, for the member's own list of devices.

    Deliberately rough. It is a label to help someone recognise their phone in
    a list, nothing reads it for logic, and a wrong guess costs nothing.
    """
    ua = user_agent or ''
    if 'iPhone' in ua:
        platform = 'iPhone'
    elif 'iPad' in ua:
        platform = 'iPad'
    elif 'Android' in ua:
        platform = 'Android'
    elif 'Windows' in ua:
        platform = 'Windows'
    elif 'Macintosh' in ua or 'Mac OS X' in ua:
        platform = 'Mac'
    elif 'CrOS' in ua:
        platform = 'Chromebook'
    elif 'Linux' in ua:
        platform = 'Linux'
    else:
        platform = ''

    # Order matters: Edge and Opera also say "Chrome", and Chrome also says
    # "Safari".
    if 'Edg/' in ua or 'EdgA/' in ua or 'EdgiOS/' in ua:
        browser = 'Edge'
    elif 'OPR/' in ua:
        browser = 'Opera'
    elif 'SamsungBrowser' in ua:
        browser = 'Samsung Internet'
    elif 'Firefox/' in ua or 'FxiOS/' in ua:
        browser = 'Firefox'
    elif 'Chrome/' in ua or 'CriOS/' in ua:
        browser = 'Chrome'
    elif 'Safari/' in ua:
        browser = 'Safari'
    else:
        browser = ''

    if browser and platform:
        return f'{browser} on {platform}'
    return browser or platform or 'Unknown device'


class NotificationSettingsView(LoginRequiredMixin, TemplateView):
    """
    One page for both halves: push on this device, and who you follow.

    Its own page rather than a section of Login & Security, which is about
    signing in. Linked from there and from the profile page.
    """

    template_name = 'auctions/notification_settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['follows'] = (
            SellerFollow.objects
            .filter(user=user)
            .select_related('seller__profile')
            .order_by('seller__first_name', 'seller__last_name', 'seller__username')
        )
        context['devices'] = PushSubscription.objects.filter(
            user=user, is_active=True
        )
        # Lets the page tell "this browser is subscribed and we will deliver to
        # it" apart from "this browser still holds a subscription we have since
        # switched off", e.g. after it was turned off from another device.
        context['active_endpoints'] = [d.endpoint for d in context['devices']]
        context['push_configured'] = push_is_configured()
        context['vapid_public_key'] = settings.VAPID_PUBLIC_KEY
        return context


class _JsonLoginRequired(LoginRequiredMixin):
    """A 403 rather than a redirect to the login page, which fetch() would follow."""

    raise_exception = True


def _json_body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@method_decorator(
    ratelimit(key='user', rate='30/h', method='POST', block=True),
    name='post',
)
class PushSubscribeView(_JsonLoginRequired, View):
    """
    Store the subscription a browser just created.

    Keyed on the endpoint, so a browser that subscribes again — after the
    member signs in as someone else on it, or the browser rotates its keys —
    updates its one row rather than adding a second that would deliver every
    alert twice.
    """

    def post(self, request):
        if not push_is_configured():
            return JsonResponse(
                {'ok': False, 'error': 'Push notifications are not set up on this site.'},
                status=503,
            )

        data = _json_body(request)
        if data is None:
            return JsonResponse({'ok': False, 'error': 'Expected JSON.'}, status=400)

        endpoint = data.get('endpoint')
        keys = data.get('keys') if isinstance(data.get('keys'), dict) else {}
        p256dh = keys.get('p256dh')
        auth = keys.get('auth')

        if not isinstance(endpoint, str) or len(endpoint) > 500:
            return JsonResponse({'ok': False, 'error': 'Invalid endpoint.'}, status=400)
        if not is_allowed_push_endpoint(endpoint):
            logger.warning(
                'User %s tried to register a push endpoint on an unrecognised host.',
                request.user.pk,
            )
            return JsonResponse(
                {'ok': False, 'error': 'That push service is not supported.'},
                status=400,
            )
        if not _valid_p256dh(p256dh) or _decode_key(auth, AUTH_BYTES) is None:
            return JsonResponse({'ok': False, 'error': 'Invalid keys.'}, status=400)

        label = describe_user_agent(request.META.get('HTTP_USER_AGENT', ''))[:255]

        subscription, created = PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh_key': p256dh,
                'auth_key': auth,
                'user_agent': label,
                'is_active': True,
            },
        )

        # Over the cap, the oldest devices go quiet first: the one in hand is
        # the one the member is using right now.
        surplus = list(
            PushSubscription.objects
            .filter(user=request.user, is_active=True)
            .order_by('-created_at', '-pk')
            .values_list('pk', flat=True)[MAX_DEVICES_PER_USER:]
        )
        if surplus:
            PushSubscription.objects.filter(pk__in=surplus).update(is_active=False)

        return JsonResponse({'ok': True, 'created': created, 'device': label})


class PushUnsubscribeView(_JsonLoginRequired, View):
    """
    Turn push off for the device the request came from.

    Matched on the endpoint and the signed-in member together, so nobody can
    switch off someone else's device by sending its endpoint.
    """

    def post(self, request):
        data = _json_body(request)
        if data is None:
            return JsonResponse({'ok': False, 'error': 'Expected JSON.'}, status=400)
        endpoint = data.get('endpoint')
        if not isinstance(endpoint, str) or not endpoint:
            return JsonResponse({'ok': False, 'error': 'Invalid endpoint.'}, status=400)

        changed = PushSubscription.objects.filter(
            user=request.user, endpoint=endpoint, is_active=True
        ).update(is_active=False)
        return JsonResponse({'ok': True, 'deactivated': changed})


class PushDeviceRemoveView(LoginRequiredMixin, View):
    """
    Turn push off for one listed device from any other device.

    For the phone that was lost or replaced: the browser that subscribed is
    not around to unsubscribe itself, so the member does it from the list.
    """

    def post(self, request, pk):
        PushSubscription.objects.filter(
            pk=pk, user=request.user, is_active=True
        ).update(is_active=False)
        messages.info(request, 'Notifications are off for that device.')
        return redirect('notification_settings')


def _back(request, fallback):
    """Return to the page the button was on, if it is one of ours; else ``fallback``."""
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback)


class FollowSellerView(LoginRequiredMixin, View):
    """Start hearing about this seller's new listings. Following twice is a no-op."""

    def post(self, request, pk):
        seller = get_object_or_404(
            User.objects.select_related('profile'), pk=pk, profile__is_seller=True
        )
        name = seller_display_name(seller)

        if seller == request.user:
            messages.info(request, "You can't follow yourself.")
            return _back(request, reverse('seller_listings', args=[seller.pk]))

        _, created = SellerFollow.objects.get_or_create(user=request.user, seller=seller)
        if created:
            messages.success(
                request,
                f"You're following {name}. We'll email you when their new "
                'listings go live.',
            )
        return _back(request, reverse('seller_listings', args=[seller.pk]))


class UnfollowSellerView(LoginRequiredMixin, View):
    """
    Stop hearing about this seller.

    Not limited to accounts still flagged as sellers: someone who has stopped
    selling must still be unfollowable from the settings list.
    """

    def post(self, request, pk):
        seller = get_object_or_404(User, pk=pk)
        deleted, _ = SellerFollow.objects.filter(
            user=request.user, seller=seller
        ).delete()
        if deleted:
            messages.info(
                request,
                f'You have unfollowed {seller_display_name(seller)}.',
            )
        return _back(request, reverse('notification_settings'))
