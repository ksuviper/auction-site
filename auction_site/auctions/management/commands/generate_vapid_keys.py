"""
Management command: generate_vapid_keys

Prints a fresh VAPID key pair for Web Push, ready to paste into .env.

    python manage.py generate_vapid_keys

A ONE-TIME setup step. Browsers tie every push subscription to the public key
they were given, so replacing the pair later silently disconnects every device
that has already turned notifications on — they would each have to turn them
on again. Generate once, keep the pair with the site's other secrets, and do
not regenerate on deploy.

Nothing is written anywhere: the keys go to the terminal only, so a private key
never ends up in a file that might be committed by accident.
"""

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.management.base import BaseCommand
from py_vapid import Vapid02
from py_vapid.utils import b64urlencode


class Command(BaseCommand):
    help = 'Print a new VAPID key pair for Web Push (one-time setup).'

    def handle(self, *args, **options):
        if settings.VAPID_PUBLIC_KEY:
            self.stderr.write(self.style.WARNING(
                'VAPID_PUBLIC_KEY is already set. Replacing it disconnects every '
                'device that has notifications turned on. Only use these if you '
                'mean to start over.\n'
            ))

        vapid = Vapid02()
        vapid.generate_keys()

        # Raw base64url, the form pywebpush and the browser's
        # applicationServerKey both take: 32 bytes of private scalar, and the
        # 65-byte uncompressed public point.
        private = b64urlencode(
            vapid.private_key.private_numbers().private_value.to_bytes(32, 'big')
        )
        public = b64urlencode(
            vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )

        self.stdout.write('# Add these to .env (keep the private key secret):')
        self.stdout.write(f'VAPID_PUBLIC_KEY={public}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={private}')
        self.stdout.write('VAPID_CLAIM_EMAIL=mailto:you@example.com')
