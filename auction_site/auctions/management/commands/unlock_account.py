"""Clear the registration gates for one account, from the command line.

Recovery tool for the case where nobody can get in through the browser — a
superuser whose email was never verified, or an account whose confirmation mail
is not arriving because SMTP is misconfigured.
"""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = (
        'Mark an account\'s email address as verified so it can log in. '
        'Pass --approve to clear the admin-approval gate at the same time.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'email',
            help='Email address (or username) of the account to unlock.',
        )
        parser.add_argument(
            '--approve',
            action='store_true',
            help='Also set is_approved on the profile (skip the approval queue).',
        )

    def handle(self, *args, **options):
        identifier = options['email']
        user = self._find_user(identifier)

        address = self._verify_email(user)
        self.stdout.write(
            self.style.SUCCESS(f'Verified {address.email} for {user.get_username()}.')
        )

        if options['approve']:
            profile = getattr(user, 'profile', None)
            if profile is None:
                raise CommandError(
                    f'{user.get_username()} has no profile row; cannot approve.'
                )
            if profile.is_approved:
                self.stdout.write('Already approved; nothing to do.')
            else:
                profile.is_approved = True
                # save() rather than update() so the approval email goes out,
                # same as approving from the admin.
                profile.save(update_fields=['is_approved'])
                self.stdout.write(self.style.SUCCESS('Approved.'))
        elif not (user.is_staff or user.is_superuser):
            profile = getattr(user, 'profile', None)
            if profile is not None and not profile.is_approved:
                self.stdout.write(
                    self.style.WARNING(
                        'Note: this account still needs admin approval before it '
                        'can log in. Re-run with --approve to clear that too.'
                    )
                )

    def _find_user(self, identifier):
        matches = list(
            User.objects.filter(email__iexact=identifier)
            | User.objects.filter(username__iexact=identifier)
        )
        if not matches:
            raise CommandError(f'No account found for "{identifier}".')
        if len(matches) > 1:
            names = ', '.join(sorted(u.get_username() for u in matches))
            raise CommandError(
                f'"{identifier}" matches several accounts ({names}); '
                'pass the username instead.'
            )
        return matches[0]

    def _verify_email(self, user):
        email = user.email
        address = EmailAddress.objects.filter(user=user).order_by(
            '-primary', 'pk'
        ).first()

        if address is None:
            if not email:
                raise CommandError(
                    f'{user.get_username()} has no email address on record; '
                    'set one in the admin first.'
                )
            address = EmailAddress(user=user, email=email, primary=True)

        clash = (
            EmailAddress.objects.filter(email__iexact=address.email, verified=True)
            .exclude(user=user)
            .exists()
        )
        if clash:
            raise CommandError(
                f'{address.email} is already verified on a different account. '
                'Resolve the duplicate before unlocking this one.'
            )

        address.verified = True
        address.save()
        return address
