"""
Push notifications: an extra channel on top of email, never instead of it.

What these pin down, in the order the prompt lists them:

* send_push_to_user() calls webpush() for every active device, switches off a
  device the push service says is gone (404/410), and never raises.
* The places that send email — outbid, auction close, Buy It Now — still send
  exactly the same email, and a push failure cannot stop it.
* Subscribing and unsubscribing a device, following and unfollowing a seller.
* "Notify followers of new listings" emails every follower of that seller, also
  pushes to the ones with a device, and nobody else hears anything.

pywebpush.webpush is mocked everywhere except EncryptionTests, which runs the
real encryption and decrypts the result as a browser would.
"""

import base64
import json
import os
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from auctions.models import (
    AuctionCategory,
    AuctionListing,
    Bid,
    Invoice,
    PushSubscription,
    SellerFollow,
    Subscription,
)
from auctions.services import notify_followers_of_new_listings, run_proxy_bids
from auctions.tests.utils import make_seller
from auctions.utils import is_allowed_push_endpoint, send_push_to_user

User = get_user_model()

PUBLIC_KEY = 'BPublicKeyForTestsOnly'
PUSH_ENV = {'VAPID_PRIVATE_KEY': 'private-key-for-tests-only'}


def _browser_public_key():
    """A real P-256 public point, as a browser hands one over."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


# Valid values for a browser subscription: a 65-byte P-256 point and a 16-byte
# auth secret, base64url without padding.
P256DH = _browser_public_key()
AUTH = base64.urlsafe_b64encode(b'\x02' * 16).rstrip(b'=').decode()
# Right length and prefix, but not a point on the curve.
NOT_ON_CURVE = base64.urlsafe_b64encode(b'\x04' + b'\x01' * 64).rstrip(b'=').decode()


def endpoint(n=1, host='fcm.googleapis.com'):
    return f'https://{host}/fcm/send/device-{n}'


def gone(status):
    """A WebPushException as pywebpush raises it for a dead subscription."""
    from pywebpush import WebPushException

    response = mock.Mock(status_code=status, text='gone', headers={})
    return WebPushException('Push failed', response=response)


def subscribe(user, n=1, active=True):
    return PushSubscription.objects.create(
        user=user,
        endpoint=endpoint(n),
        p256dh_key=P256DH,
        auth_key=AUTH,
        user_agent='Chrome on Android',
        is_active=active,
    )


class PushTestCase(TestCase):
    """VAPID configured, and webpush mocked, for every test unless overridden."""

    def setUp(self):
        super().setUp()
        settings_override = override_settings(
            VAPID_PUBLIC_KEY=PUBLIC_KEY,
            VAPID_CLAIM_EMAIL='mailto:admin@example.com',
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        env = mock.patch.dict(os.environ, PUSH_ENV)
        env.start()
        self.addCleanup(env.stop)

        patcher = mock.patch('pywebpush.webpush')
        self.webpush = patcher.start()
        self.addCleanup(patcher.stop)

    def pushed_to(self):
        """(endpoint, payload) for every webpush() call made."""
        calls = []
        for call in self.webpush.call_args_list:
            info = call.kwargs['subscription_info']
            calls.append((info['endpoint'], json.loads(call.kwargs['data'])))
        return calls


# ── send_push_to_user ────────────────────────────────────────────────────────

class SendPushTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('member', 'member@example.com', 'pw')

    def test_it_calls_webpush_for_an_active_subscription(self):
        subscribe(self.user)

        sent = send_push_to_user(self.user, 'Hello', 'Body text', url='/listing/4/')

        self.assertEqual(sent, 1)
        self.webpush.assert_called_once()
        kwargs = self.webpush.call_args.kwargs
        self.assertEqual(kwargs['subscription_info'], {
            'endpoint': endpoint(1), 'keys': {'p256dh': P256DH, 'auth': AUTH},
        })
        self.assertEqual(
            json.loads(kwargs['data']),
            {'title': 'Hello', 'body': 'Body text', 'url': '/listing/4/'},
        )
        self.assertEqual(kwargs['vapid_private_key'], PUSH_ENV['VAPID_PRIVATE_KEY'])
        self.assertEqual(kwargs['vapid_claims'], {'sub': 'mailto:admin@example.com'})
        self.assertTrue(kwargs['timeout'])

    def test_every_active_device_gets_it_and_inactive_ones_do_not(self):
        subscribe(self.user, 1)
        subscribe(self.user, 2)
        subscribe(self.user, 3, active=False)

        self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 2)
        self.assertEqual(
            sorted(e for e, _ in self.pushed_to()), [endpoint(1), endpoint(2)]
        )

    def test_claims_are_not_shared_between_devices(self):
        """pywebpush writes an 'aud' into the dict it is given."""
        subscribe(self.user, 1)
        subscribe(self.user, 2)

        def record(**kwargs):
            kwargs['vapid_claims']['aud'] = kwargs['subscription_info']['endpoint']

        self.webpush.side_effect = record
        send_push_to_user(self.user, 'T', 'B')

        claims = [c.kwargs['vapid_claims'] for c in self.webpush.call_args_list]
        self.assertIsNot(claims[0], claims[1])

    def test_an_expired_subscription_is_marked_inactive(self):
        for n, status in enumerate((404, 410), start=1):
            with self.subTest(status=status):
                sub = subscribe(self.user, n)
                self.webpush.side_effect = gone(status)

                self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 0)

                sub.refresh_from_db()
                self.assertFalse(sub.is_active)

    def test_a_temporary_failure_keeps_the_subscription(self):
        sub = subscribe(self.user)
        for status in (429, 500, 503, 403):
            with self.subTest(status=status):
                self.webpush.side_effect = gone(status)
                send_push_to_user(self.user, 'T', 'B')
                sub.refresh_from_db()
                self.assertTrue(sub.is_active)

    def test_a_general_failure_does_not_raise(self):
        subscribe(self.user)
        self.webpush.side_effect = ConnectionError('network down')

        with self.assertLogs('auctions.utils', level='ERROR'):
            self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 0)

    def test_one_failing_device_does_not_stop_the_next(self):
        subscribe(self.user, 1)
        subscribe(self.user, 2)
        self.webpush.side_effect = [RuntimeError('boom'), None]

        with self.assertLogs('auctions.utils', level='ERROR'):
            self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 1)

    def test_no_subscription_is_a_quiet_no_op(self):
        self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 0)
        self.webpush.assert_not_called()

    def test_without_vapid_keys_nothing_is_attempted(self):
        subscribe(self.user)
        with override_settings(VAPID_PUBLIC_KEY=''):
            self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 0)
        with mock.patch.dict(os.environ, {'VAPID_PRIVATE_KEY': ''}):
            self.assertEqual(send_push_to_user(self.user, 'T', 'B'), 0)
        self.webpush.assert_not_called()

    def test_none_is_a_no_op(self):
        self.assertEqual(send_push_to_user(None, 'T', 'B'), 0)

    def test_the_private_key_is_read_from_the_environment_not_settings(self):
        from django.conf import settings

        self.assertFalse(hasattr(settings, 'VAPID_PRIVATE_KEY'))


class EncryptionTests(TestCase):
    """
    The real thing, minus the network: pywebpush encrypts with keys made the
    way generate_vapid_keys makes them, and the result decrypts the way a
    browser would. Catches a key in the wrong format, which mocks cannot.
    """

    def test_a_payload_survives_the_round_trip(self):
        import http_ece
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        out = StringIO()
        call_command('generate_vapid_keys', stdout=out, stderr=StringIO())
        env = dict(
            line.split('=', 1) for line in out.getvalue().splitlines()
            if '=' in line and not line.startswith('#')
        )

        # What a browser does on subscribe: a key pair and an auth secret.
        browser_key = ec.generate_private_key(ec.SECP256R1())
        browser_public = browser_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        auth_secret = os.urandom(16)

        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

        user = User.objects.create_user('enc', 'enc@example.com', 'pw')
        PushSubscription.objects.create(
            user=user, endpoint=endpoint(), p256dh_key=b64(browser_public),
            auth_key=b64(auth_secret),
        )

        response = mock.Mock(status_code=201, text='', headers={})
        with override_settings(
            VAPID_PUBLIC_KEY=env['VAPID_PUBLIC_KEY'],
            VAPID_CLAIM_EMAIL='mailto:admin@example.com',
        ), mock.patch.dict(os.environ, {'VAPID_PRIVATE_KEY': env['VAPID_PRIVATE_KEY']}), \
                mock.patch('requests.post', return_value=response) as post:
            sent = send_push_to_user(user, 'Outbid', 'On Lavender Lady', url='/listing/9/')

        self.assertEqual(sent, 1)
        call = post.call_args
        self.assertEqual(call.args[0], endpoint())
        self.assertIn('vapid t=', call.kwargs['headers']['authorization'])
        self.assertIn(env['VAPID_PUBLIC_KEY'], call.kwargs['headers']['authorization'])

        plaintext = http_ece.decrypt(
            call.kwargs['data'],
            private_key=browser_key,
            auth_secret=auth_secret,
            version='aes128gcm',
        )
        self.assertEqual(
            json.loads(plaintext),
            {'title': 'Outbid', 'body': 'On Lavender Lady', 'url': '/listing/9/'},
        )


class EndpointAllowListTests(TestCase):
    def test_the_real_push_services_are_accepted(self):
        for url in (
            'https://fcm.googleapis.com/fcm/send/abc',
            'https://updates.push.services.mozilla.com/wpush/v2/abc',
            'https://web.push.apple.com/QGx',
            'https://wns2-par02p.notify.windows.com/w/?token=abc',
        ):
            with self.subTest(url=url):
                self.assertTrue(is_allowed_push_endpoint(url))

    def test_anything_else_is_refused(self):
        for url in (
            'http://fcm.googleapis.com/fcm/send/abc',      # not HTTPS
            'https://127.0.0.1/admin/',
            'https://localhost:8000/',
            'https://evil-fcm.googleapis.com.attacker.example/',
            'https://notfcm.googleapis.com/',
            'https://169.254.169.254/latest/meta-data/',
            'javascript:alert(1)',
            '',
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(is_allowed_push_endpoint(url))


# ── The existing email trigger points ────────────────────────────────────────

class _AuctionFixture:
    def make_listing(self, seller, **overrides):
        now = timezone.now()
        fields = dict(
            title='Lavender Lady',
            category=self.category,
            seller=seller,
            start_price='10.00',
            listing_type='auction',
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=7),
            is_active=True,
        )
        fields.update(overrides)
        return AuctionListing.objects.create(**fields)

    def member(self, username):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        Subscription.objects.create(user=user, plan='monthly', status='active')
        user.profile.country = 'US'
        user.profile.save(update_fields=['country'])
        return user


class OutbidTests(_AuctionFixture, PushTestCase):
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('grower')
        self.listing = self.make_listing(self.seller)
        self.loser = self.member('loser')
        self.winner = self.member('winner')
        from auctions.models import ProxyBid

        ProxyBid.objects.create(listing=self.listing, bidder=self.loser, max_amount='15')
        ProxyBid.objects.create(listing=self.listing, bidder=self.winner, max_amount='30')

    def outbid_emails(self):
        return [m for m in mail.outbox if m.subject.startswith('You were outbid')]

    def test_outbid_sends_the_email_and_then_a_push(self):
        subscribe(self.loser)

        with self.captureOnCommitCallbacks(execute=True):
            run_proxy_bids(self.listing)

        self.assertEqual(len(self.outbid_emails()), 1)
        self.assertEqual(self.outbid_emails()[0].to, ['loser@example.com'])
        ((sent_to, payload),) = self.pushed_to()
        self.assertEqual(sent_to, endpoint())
        self.assertEqual(payload['title'], "You've been outbid")
        self.assertEqual(payload['body'], 'Someone bid higher on Lavender Lady.')
        self.assertEqual(payload['url'], f'/listing/{self.listing.pk}/')

    def test_a_push_failure_leaves_the_bid_and_the_email_alone(self):
        subscribe(self.loser)
        self.webpush.side_effect = RuntimeError('push service exploded')

        with self.assertLogs('auctions.utils', level='ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                placed_for = run_proxy_bids(self.listing)

        self.assertEqual(placed_for, self.winner)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.current_bid, Decimal('16.00'))
        self.assertEqual(len(self.outbid_emails()), 1)

    def test_the_push_waits_for_the_bid_to_commit(self):
        """No network call while the listing row is locked."""
        subscribe(self.loser)

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run_proxy_bids(self.listing)

        self.webpush.assert_not_called()
        self.assertEqual(len(self.outbid_emails()), 1)
        for callback in callbacks:
            callback()
        self.webpush.assert_called_once()

    def test_a_bidder_without_push_gets_exactly_the_old_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            run_proxy_bids(self.listing)

        self.assertEqual(len(self.outbid_emails()), 1)
        self.webpush.assert_not_called()

    def test_through_the_bid_view_too(self):
        """The view path, end to end: a manual bid that displaces a proxy."""
        subscribe(self.winner)
        challenger = self.member('challenger')
        self.client.force_login(challenger)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('place_bid', args=[self.listing.pk]), {'amount': '50'}
            )

        self.assertTrue(
            any(e.to == ['winner@example.com'] for e in self.outbid_emails())
        )
        self.assertIn(endpoint(), [e for e, _ in self.pushed_to()])


class AuctionCloseTests(_AuctionFixture, PushTestCase):
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('closer')
        self.listing = self.make_listing(
            self.seller, ends_at=timezone.now() - timedelta(minutes=1)
        )
        self.bidder = self.member('topbidder')
        Bid.objects.create(listing=self.listing, bidder=self.bidder, amount='25.00')

    def close(self):
        out = StringIO()
        call_command('close_ended_auctions', stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_winner_and_seller_get_pushes_after_the_existing_emails(self):
        subscribe(self.bidder, 1)
        subscribe(self.seller, 2)

        output = self.close()

        self.assertIn('closed=1', output)
        invoice = Invoice.objects.get(listing=self.listing)
        pushes = dict(self.pushed_to())
        self.assertEqual(pushes[endpoint(1)]['title'], 'You won Lavender Lady!')
        self.assertEqual(
            pushes[endpoint(1)]['url'], reverse('invoice_detail', args=[invoice.pk])
        )
        self.assertEqual(pushes[endpoint(2)]['title'], 'Your listing Lavender Lady sold.')
        self.assertEqual(
            pushes[endpoint(2)]['url'], reverse('listing_detail', args=[self.listing.pk])
        )
        # The seller's email is unchanged, and the winner still gets no email
        # at close — combined invoicing is their notification.
        subjects = [m.subject for m in mail.outbox]
        self.assertIn('Your auction ended: Lavender Lady', subjects)
        self.assertFalse(any(self.bidder.email in m.to for m in mail.outbox))

    def test_a_push_failure_does_not_count_as_a_failed_close(self):
        subscribe(self.bidder)
        self.webpush.side_effect = RuntimeError('down')

        with self.assertLogs('auctions.utils', level='ERROR'):
            output = self.close()

        self.assertIn('closed=1, invoiced=1, errors=0', output)
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.is_closed)
        self.assertIn('Your auction ended: Lavender Lady', [m.subject for m in mail.outbox])

    def test_even_an_unexpected_error_in_the_push_step_is_contained(self):
        with mock.patch(
            'auctions.management.commands.close_ended_auctions.send_push_to_user',
            side_effect=RuntimeError('bug'),
        ), self.assertLogs(
            'auctions.management.commands.close_ended_auctions', level='ERROR'
        ):
            output = self.close()

        self.assertIn('errors=0', output)

    def test_no_bids_means_no_pushes(self):
        Bid.objects.all().delete()
        subscribe(self.seller)

        self.close()

        self.webpush.assert_not_called()


class BuyNowTests(_AuctionFixture, PushTestCase):
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller = make_seller('shop')
        self.listing = self.make_listing(
            self.seller,
            listing_type='buy_now',
            buy_now_price='12.00',
            quantity_available=3,
        )
        self.buyer = self.member('shopper')

    def buy(self):
        self.client.force_login(self.buyer)
        return self.client.post(reverse('buy_now', args=[self.listing.pk]), {'quantity': 1})

    def test_buyer_and_seller_get_pushes_and_the_emails_are_unchanged(self):
        subscribe(self.buyer, 1)
        subscribe(self.seller, 2)

        self.buy()

        invoice = Invoice.objects.get(listing=self.listing)
        pushes = dict(self.pushed_to())
        self.assertEqual(pushes[endpoint(1)]['title'], 'Purchase confirmed: Lavender Lady')
        self.assertEqual(
            pushes[endpoint(1)]['url'], reverse('invoice_detail', args=[invoice.pk])
        )
        self.assertEqual(pushes[endpoint(2)]['title'], 'Your item sold: Lavender Lady')
        self.assertIn('2 still in stock', pushes[endpoint(2)]['body'])
        self.assertEqual(
            [m.subject for m in mail.outbox if m.to == ['shop@example.com']],
            ['Your item sold: Lavender Lady'],
        )

    def test_a_push_failure_does_not_undo_the_purchase(self):
        subscribe(self.buyer)
        self.webpush.side_effect = RuntimeError('down')

        with self.assertLogs('auctions.utils', level='ERROR'):
            response = self.buy()

        invoice = Invoice.objects.get(listing=self.listing)
        self.assertRedirects(
            response, reverse('invoice_detail', args=[invoice.pk]),
            fetch_redirect_response=False,
        )
        self.assertIn('Your item sold: Lavender Lady', [m.subject for m in mail.outbox])


# ── Subscribing a device ─────────────────────────────────────────────────────

class SubscribeEndpointTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('device', 'device@example.com', 'pw')
        self.client.force_login(self.user)
        self.url = reverse('push_subscribe')

    def post(self, payload, **extra):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json', **extra
        )

    def payload(self, n=1, **overrides):
        data = {'endpoint': endpoint(n), 'keys': {'p256dh': P256DH, 'auth': AUTH}}
        data.update(overrides)
        return data

    def test_it_creates_a_push_subscription(self):
        response = self.post(
            self.payload(),
            HTTP_USER_AGENT=(
                'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36'
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ok'], True)
        sub = PushSubscription.objects.get()
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.endpoint, endpoint())
        self.assertEqual((sub.p256dh_key, sub.auth_key), (P256DH, AUTH))
        self.assertEqual(sub.user_agent, 'Chrome on Android')
        self.assertTrue(sub.is_active)

    def test_resubscribing_the_same_browser_updates_rather_than_duplicates(self):
        self.post(self.payload())
        PushSubscription.objects.update(is_active=False)
        new_auth = base64.urlsafe_b64encode(b'\x09' * 16).rstrip(b'=').decode()

        self.post(self.payload(keys={'p256dh': P256DH, 'auth': new_auth}))

        sub = PushSubscription.objects.get()
        self.assertEqual(sub.auth_key, new_auth)
        self.assertTrue(sub.is_active)

    def test_a_shared_browser_moves_to_whoever_is_signed_in(self):
        self.post(self.payload())
        other = User.objects.create_user('other', 'other@example.com', 'pw')
        self.client.force_login(other)

        self.post(self.payload())

        self.assertEqual(PushSubscription.objects.get().user, other)

    def test_bad_input_is_refused(self):
        cases = {
            'not json': None,
            'no endpoint': self.payload(endpoint=''),
            'internal host': self.payload(endpoint='https://127.0.0.1/admin/'),
            'plain http': self.payload(endpoint='http://fcm.googleapis.com/x'),
            'short p256dh': self.payload(keys={'p256dh': AUTH, 'auth': AUTH}),
            'short auth': self.payload(keys={'p256dh': P256DH, 'auth': 'AAAA'}),
            'no keys': self.payload(keys=None),
            'not base64': self.payload(keys={'p256dh': '!!!', 'auth': AUTH}),
            'not on the curve': self.payload(keys={'p256dh': NOT_ON_CURVE, 'auth': AUTH}),
            'too long': self.payload(endpoint=endpoint() + 'x' * 500),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                if payload is None:
                    response = self.client.post(
                        self.url, 'not json', content_type='application/json'
                    )
                else:
                    response = self.post(payload)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_device_count_is_capped_oldest_off_first(self):
        from auctions.notification_views import MAX_DEVICES_PER_USER

        for n in range(MAX_DEVICES_PER_USER + 2):
            self.post(self.payload(n))

        active = PushSubscription.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active.count(), MAX_DEVICES_PER_USER)
        self.assertFalse(active.filter(endpoint=endpoint(0)).exists())
        self.assertTrue(active.filter(endpoint=endpoint(MAX_DEVICES_PER_USER + 1)).exists())

    def test_it_needs_a_signed_in_member(self):
        self.client.logout()
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_it_is_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_without_vapid_keys_it_says_so(self):
        with override_settings(VAPID_PUBLIC_KEY=''):
            response = self.post(self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PushSubscription.objects.exists())

    def test_csrf_is_enforced(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            self.url, json.dumps(self.payload()), content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)


class UnsubscribeEndpointTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('leaver', 'leaver@example.com', 'pw')
        self.client.force_login(self.user)
        self.url = reverse('push_unsubscribe')

    def post(self, endpoint_value):
        return self.client.post(
            self.url, json.dumps({'endpoint': endpoint_value}),
            content_type='application/json',
        )

    def test_it_deactivates_this_device(self):
        sub = subscribe(self.user)

        response = self.post(sub.endpoint)

        self.assertEqual(response.json(), {'ok': True, 'deactivated': 1})
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    def test_it_cannot_switch_off_someone_elses_device(self):
        other = User.objects.create_user('victim', 'victim@example.com', 'pw')
        sub = subscribe(other)

        response = self.post(sub.endpoint)

        self.assertEqual(response.json()['deactivated'], 0)
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)

    def test_a_device_can_be_turned_off_from_the_list(self):
        sub = subscribe(self.user)
        other_sub = subscribe(
            User.objects.create_user('x', 'x@example.com', 'pw'), n=2
        )

        self.client.post(reverse('push_device_remove', args=[sub.pk]))
        self.client.post(reverse('push_device_remove', args=[other_sub.pk]))

        sub.refresh_from_db()
        other_sub.refresh_from_db()
        self.assertFalse(sub.is_active)
        self.assertTrue(other_sub.is_active)


# ── Following sellers ────────────────────────────────────────────────────────

class FollowTests(TestCase):
    def setUp(self):
        self.seller = make_seller('rosegrower', first_name='Rose', last_name='Grower')
        self.user = User.objects.create_user('fan', 'fan@example.com', 'pw')
        self.client.force_login(self.user)

    def follow(self, seller=None, **data):
        return self.client.post(
            reverse('follow_seller', args=[(seller or self.seller).pk]), data
        )

    def test_following_creates_a_seller_follow(self):
        response = self.follow()

        self.assertRedirects(
            response, reverse('seller_listings', args=[self.seller.pk]),
            fetch_redirect_response=False,
        )
        follow = SellerFollow.objects.get()
        self.assertEqual((follow.user, follow.seller), (self.user, self.seller))

    def test_following_twice_does_not_duplicate(self):
        self.follow()
        self.follow()
        self.assertEqual(SellerFollow.objects.count(), 1)

    def test_the_database_refuses_a_duplicate_too(self):
        SellerFollow.objects.create(user=self.user, seller=self.seller)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SellerFollow.objects.create(user=self.user, seller=self.seller)

    def test_you_cannot_follow_yourself(self):
        self.client.force_login(self.seller)
        self.follow()
        self.assertFalse(SellerFollow.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            SellerFollow.objects.create(user=self.seller, seller=self.seller)

    def test_only_sellers_can_be_followed(self):
        buyer = User.objects.create_user('justabuyer', 'b@example.com', 'pw')
        self.assertEqual(self.follow(seller=buyer).status_code, 404)
        self.assertFalse(SellerFollow.objects.exists())

    def test_unfollow_removes_it(self):
        self.follow()
        response = self.client.post(reverse('unfollow_seller', args=[self.seller.pk]))

        self.assertFalse(SellerFollow.objects.exists())
        self.assertRedirects(
            response, reverse('notification_settings'), fetch_redirect_response=False
        )

    def test_next_returns_to_the_page_but_never_off_site(self):
        page = reverse('seller_listings', args=[self.seller.pk])
        self.assertRedirects(
            self.follow(next=page), page, fetch_redirect_response=False
        )
        response = self.client.post(
            reverse('unfollow_seller', args=[self.seller.pk]),
            {'next': 'https://evil.example/'},
        )
        self.assertEqual(response['Location'], reverse('notification_settings'))

    def test_it_needs_a_signed_in_member_and_a_post(self):
        self.assertEqual(
            self.client.get(reverse('follow_seller', args=[self.seller.pk])).status_code,
            405,
        )
        self.client.logout()
        response = self.follow()
        self.assertIn(reverse('account_login'), response['Location'])
        self.assertFalse(SellerFollow.objects.exists())

    def test_following_does_not_need_push(self):
        """Independent of devices: follow with none, and still be a follower."""
        self.follow()
        self.assertFalse(PushSubscription.objects.exists())
        self.assertTrue(SellerFollow.objects.filter(user=self.user).exists())


class SellerPageButtonTests(TestCase):
    def setUp(self):
        self.seller = make_seller('daylilyfarm', first_name='Daylily', last_name='Farm')
        self.user = User.objects.create_user('browser', 'browser@example.com', 'pw')
        self.url = reverse('seller_listings', args=[self.seller.pk])

    def test_a_member_sees_follow_then_following(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.url), 'Follow this seller for new listing alerts')

        SellerFollow.objects.create(user=self.user, seller=self.seller)
        body = self.client.get(self.url)
        self.assertContains(body, 'Following ✓')
        self.assertContains(body, reverse('unfollow_seller', args=[self.seller.pk]))

    def test_a_visitor_is_asked_to_log_in(self):
        self.assertContains(self.client.get(self.url), 'Log in to follow this seller')

    def test_a_seller_does_not_see_it_on_their_own_page(self):
        self.client.force_login(self.seller)
        body = self.client.get(self.url).content.decode()
        self.assertNotIn('follow_seller', body)
        self.assertNotIn('Follow this seller', body)


class SettingsPageTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('settings', 'settings@example.com', 'pw')
        self.client.force_login(self.user)
        self.url = reverse('notification_settings')

    def test_it_lists_followed_sellers_with_unfollow(self):
        seller = make_seller('peony', first_name='Peony', last_name='Patch')
        SellerFollow.objects.create(user=self.user, seller=seller)

        body = self.client.get(self.url)

        self.assertContains(body, 'Peony Patch')
        self.assertContains(body, reverse('unfollow_seller', args=[seller.pk]))
        self.assertContains(body, 'aria-label="Unfollow Peony Patch"')

    def test_it_renders_every_state_for_the_script_to_choose(self):
        body = self.client.get(self.url).content.decode()
        for state in ('ios-install', 'unsupported', 'denied', 'enable', 'enabled'):
            with self.subTest(state=state):
                self.assertIn(f'data-push-state="{state}"', body)
        self.assertIn(f'data-vapid-key="{PUBLIC_KEY}"', body)
        self.assertIn('Enable Notifications', body)
        self.assertIn('Add to Home Screen', body)
        self.assertIn('js/notifications.js', body)

    def test_email_is_described_as_always_on(self):
        self.assertContains(self.client.get(self.url), 'Always on')

    def test_without_keys_there_is_no_enable_button(self):
        with override_settings(VAPID_PUBLIC_KEY=''):
            body = self.client.get(self.url).content.decode()
        self.assertNotIn('Enable Notifications', body)
        self.assertIn("aren't available on this site yet", body)

    def test_only_your_own_devices_are_listed(self):
        subscribe(self.user, 1)
        other = User.objects.create_user('o', 'o@example.com', 'pw')
        PushSubscription.objects.create(
            user=other, endpoint=endpoint(2), p256dh_key=P256DH, auth_key=AUTH,
            user_agent='Firefox on Windows',
        )

        body = self.client.get(self.url).content.decode()

        self.assertIn('Chrome on Android', body)
        self.assertNotIn('Firefox on Windows', body)
        self.assertNotIn(endpoint(2), body)

    def test_it_is_reachable_from_the_profile_and_security_pages(self):
        for name in ('profile', 'security_settings'):
            with self.subTest(page=name):
                self.assertContains(self.client.get(reverse(name)), self.url)

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertIn(reverse('account_login'), self.client.get(self.url)['Location'])


# ── Notify followers of new listings ─────────────────────────────────────────

class NotifyFollowersTests(_AuctionFixture, PushTestCase):
    def setUp(self):
        super().setUp()
        self.category = AuctionCategory.objects.create(name='Daylilies')
        self.seller_a = make_seller('sellera', first_name='Alice', last_name='Acres')
        self.seller_b = make_seller('sellerb', first_name='Bob', last_name='Blooms')
        self.make_listing(self.seller_a, title='Ruby Spider')
        self.make_listing(self.seller_a, title='Moonlit Masquerade')

        # Follows A and has push on.
        self.with_push = User.objects.create_user('pushfan', 'pushfan@example.com', 'pw')
        SellerFollow.objects.create(user=self.with_push, seller=self.seller_a)
        subscribe(self.with_push, 1)

        # Follows A, never turned push on.
        self.email_only = User.objects.create_user('mailfan', 'mailfan@example.com', 'pw')
        SellerFollow.objects.create(user=self.email_only, seller=self.seller_a)

        # Follows B only — and has push on, so silence is meaningful.
        self.b_fan = User.objects.create_user('bfan', 'bfan@example.com', 'pw')
        SellerFollow.objects.create(user=self.b_fan, seller=self.seller_b)
        subscribe(self.b_fan, 2)

        # Follows nobody, push on.
        self.nobody = User.objects.create_user('nobody', 'nobody@example.com', 'pw')
        subscribe(self.nobody, 3)

        mail.outbox.clear()

    def notified_emails(self):
        return [m for m in mail.outbox if m.subject == 'New listings from Alice Acres']

    def test_every_follower_of_a_gets_an_email_regardless_of_push(self):
        notify_followers_of_new_listings(self.seller_a)

        recipients = sorted(m.to[0] for m in self.notified_emails())
        self.assertEqual(recipients, ['mailfan@example.com', 'pushfan@example.com'])
        body = self.notified_emails()[0].body
        self.assertIn('Alice Acres has new plants up for auction.', body)
        self.assertIn('Ruby Spider', body)
        self.assertIn('Moonlit Masquerade', body)
        self.assertIn(reverse('seller_listings', args=[self.seller_a.pk]), body)
        self.assertIn('Unfollow', body)

    def test_push_goes_only_to_followers_with_a_device(self):
        counts = notify_followers_of_new_listings(self.seller_a)

        ((sent_to, payload),) = self.pushed_to()
        self.assertEqual(sent_to, endpoint(1))
        self.assertEqual(payload['title'], 'New listings from Alice Acres')
        self.assertEqual(payload['url'], reverse('seller_listings', args=[self.seller_a.pk]))
        self.assertEqual(counts, {'followers': 2, 'emailed': 2, 'pushed': 1})

    def test_followers_of_b_and_followers_of_nobody_hear_nothing(self):
        notify_followers_of_new_listings(self.seller_a)

        everyone_emailed = {address for m in mail.outbox for address in m.to}
        self.assertNotIn('bfan@example.com', everyone_emailed)
        self.assertNotIn('nobody@example.com', everyone_emailed)
        pushed = {e for e, _ in self.pushed_to()}
        self.assertNotIn(endpoint(2), pushed)
        self.assertNotIn(endpoint(3), pushed)

    def test_a_push_failure_still_leaves_every_email_sent(self):
        self.webpush.side_effect = RuntimeError('push service down')

        with self.assertLogs('auctions.utils', level='ERROR'):
            counts = notify_followers_of_new_listings(self.seller_a)

        self.assertEqual(len(self.notified_emails()), 2)
        self.assertEqual(counts['pushed'], 0)

    def test_without_vapid_keys_the_emails_still_go(self):
        with override_settings(VAPID_PUBLIC_KEY=''):
            notify_followers_of_new_listings(self.seller_a)

        self.assertEqual(len(self.notified_emails()), 2)
        self.webpush.assert_not_called()

    def test_a_deactivated_account_is_skipped(self):
        User.objects.filter(pk=self.email_only.pk).update(is_active=False)

        notify_followers_of_new_listings(self.seller_a)

        self.assertEqual(
            [m.to for m in self.notified_emails()], [['pushfan@example.com']]
        )

    def test_it_records_when_it_was_sent(self):
        notify_followers_of_new_listings(self.seller_a)
        self.seller_a.profile.refresh_from_db()
        self.assertIsNotNone(self.seller_a.profile.followers_notified_at)

    def test_adding_a_listing_notifies_no_one_by_itself(self):
        """Manual, per seller: never automatic per plant."""
        self.make_listing(self.seller_a, title='Yet Another Plant')
        self.assertEqual(self.notified_emails(), [])
        self.webpush.assert_not_called()


class NotifyFollowersEntryPointTests(PushTestCase):
    """The Users-list admin action and the weekly setup button share one page."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'pw')
        self.client.force_login(self.admin)
        self.seller = make_seller('sellerc', first_name='Cara', last_name='Cultivars')
        self.fan = User.objects.create_user('cfan', 'cfan@example.com', 'pw')
        SellerFollow.objects.create(user=self.fan, seller=self.seller)
        self.confirm_url = reverse('weekly_setup_notify_followers', args=[self.seller.pk])
        mail.outbox.clear()

    def run_action(self, *users):
        return self.client.post(reverse('admin:auth_user_changelist'), {
            'action': 'notify_followers',
            '_selected_action': [u.pk for u in users],
        })

    def emails(self):
        return [m for m in mail.outbox if m.subject.startswith('New listings from')]

    def test_the_admin_action_goes_to_the_confirmation_page_without_sending(self):
        response = self.run_action(self.seller)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(self.confirm_url))
        self.assertEqual(self.emails(), [])

    def test_the_action_is_for_one_seller_at_a_time(self):
        other = make_seller('sellerd')
        response = self.run_action(self.seller, other)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('notify-followers', response['Location'])
        self.assertEqual(self.emails(), [])

    def test_the_action_refuses_a_non_seller(self):
        response = self.run_action(self.fan)
        self.assertNotIn('notify-followers', response['Location'])

    def test_the_confirmation_page_says_how_many_will_be_told(self):
        body = self.client.get(self.confirm_url)
        self.assertContains(body, '1 follower')
        self.assertContains(body, 'Send to 1 follower')
        self.assertEqual(self.emails(), [])

    def test_confirming_sends_and_returns(self):
        back = reverse('admin:auth_user_changelist')
        response = self.client.post(self.confirm_url, {'next': back})

        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertEqual([m.to for m in self.emails()], [['cfan@example.com']])

    def test_it_warns_when_followers_were_already_told(self):
        self.client.post(self.confirm_url)
        self.assertContains(self.client.get(self.confirm_url), 'last notified')

    def test_the_weekly_setup_pages_link_to_it(self):
        for name in ('weekly_setup_listings', 'weekly_setup_done'):
            with self.subTest(page=name):
                body = self.client.get(reverse(name, args=[self.seller.pk]))
                self.assertContains(body, 'Notify followers of new listings')
                self.assertContains(body, self.confirm_url)

    def test_staff_only(self):
        self.client.force_login(self.fan)
        self.assertEqual(self.client.get(self.confirm_url).status_code, 403)
        self.assertEqual(self.client.post(self.confirm_url).status_code, 403)
        self.assertEqual(self.emails(), [])


class AdminRegistrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.force_login(self.admin)

    def test_both_changelists_load(self):
        user = User.objects.create_user('someone', 's@example.com', 'pw')
        seller = make_seller('aseller')
        subscribe(user)
        SellerFollow.objects.create(user=user, seller=seller)
        for name in ('pushsubscription', 'sellerfollow'):
            with self.subTest(model=name):
                url = reverse(f'admin:auctions_{name}_changelist')
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.get(url + '?q=someone').status_code, 200)

    def test_both_are_in_the_sidebar(self):
        body = self.client.get(reverse('admin:index')).content.decode()
        self.assertIn(reverse('admin:auctions_pushsubscription_changelist'), body)
        self.assertIn(reverse('admin:auctions_sellerfollow_changelist'), body)


# ── The service worker ───────────────────────────────────────────────────────

class ServiceWorkerTests(TestCase):
    def setUp(self):
        self.worker = self.client.get('/sw.js').content.decode()

    def test_it_handles_push_and_notification_clicks(self):
        self.assertIn("addEventListener('push'", self.worker)
        self.assertIn("addEventListener('notificationclick'", self.worker)
        self.assertIn('showNotification', self.worker)
        self.assertIn('clients.openWindow', self.worker)

    def test_it_uses_the_generated_icon_not_a_static_path(self):
        self.assertIn(reverse('pwa_icon', args=[192]), self.worker)
        self.assertNotIn('/static/icons/', self.worker)

    def test_the_offline_fallback_is_unchanged(self):
        self.assertIn("addEventListener('fetch'", self.worker)
        self.assertIn(reverse('pwa_offline'), self.worker)
        self.assertIn("request.mode !== 'navigate'", self.worker)

    def test_still_nothing_cached_but_the_offline_page(self):
        self.assertEqual(self.worker.count('cache.add('), 1)
        self.assertNotIn('cache.addAll', self.worker)
        self.assertNotIn('cache.put', self.worker)

    def test_a_notification_can_only_open_this_site(self):
        self.assertIn('url.origin === self.location.origin', self.worker)

    def test_the_version_was_bumped(self):
        from auction_site.pwa import SERVICE_WORKER_VERSION

        self.assertNotEqual(SERVICE_WORKER_VERSION, 'v1')
        self.assertIn(f"const VERSION = '{SERVICE_WORKER_VERSION}'", self.worker)


class NoKeysCommittedTests(TestCase):
    """The example file documents the keys; it must never carry real ones."""

    def test_env_example_has_blank_vapid_keys(self):
        from pathlib import Path

        from django.conf import settings

        example = Path(settings.BASE_DIR).parent / '.env.example'
        lines = example.read_text().splitlines()
        self.assertIn('VAPID_PUBLIC_KEY=', lines)
        self.assertIn('VAPID_PRIVATE_KEY=', lines)
