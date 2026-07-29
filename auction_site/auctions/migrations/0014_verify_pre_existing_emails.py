from django.conf import settings
from django.db import migrations


def verify_pre_existing_emails(apps, schema_editor):
    """
    Mark every account that predates mandatory verification as verified.

    ACCOUNT_EMAIL_VERIFICATION went from 'optional' to 'mandatory'. Applied to an
    existing database that change locks out:

      * accounts created by ``createsuperuser``, which knows nothing about
        allauth and leaves no EmailAddress row at all — including, typically,
        the only account able to fix the problem; and
      * every member who signed up while verification was optional and never
        confirmed, since their row exists with verified=False.

    Neither group can recover on its own, so grandfather both: the gate is meant
    to stop new fake sign-ups, not to evict known members. Anyone whose address
    looks suspicious can be un-verified individually in the admin afterwards.

    Two allauth constraints have to be respected, and they are the reason this
    walks rows instead of issuing one UPDATE:
      * unique_verified_email — UNIQUE(email) WHERE verified, so an address
        already verified by another account must be left alone; and
      * unique_primary_email — UNIQUE(user, primary) WHERE primary.
    Ordering by pk means the oldest account wins a contested address.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL)
    EmailAddress = apps.get_model('account', 'EmailAddress')

    taken = {
        email.lower()
        for email in EmailAddress.objects.filter(verified=True).values_list(
            'email', flat=True
        )
        if email
    }

    # 1. Rows that exist but were never confirmed.
    for address in EmailAddress.objects.filter(verified=False).order_by('pk'):
        if not address.email or address.email.lower() in taken:
            continue
        address.verified = True
        address.save(update_fields=['verified'])
        taken.add(address.email.lower())

    # 2. Users with no row at all — createsuperuser accounts.
    has_row = set(EmailAddress.objects.values_list('user_id', flat=True))
    candidates = (
        User.objects.exclude(pk__in=has_row).exclude(email='').order_by('pk')
    )
    for user in candidates:
        if user.email.lower() in taken:
            continue
        EmailAddress.objects.create(
            user=user, email=user.email, verified=True, primary=True
        )
        taken.add(user.email.lower())


class Migration(migrations.Migration):

    dependencies = [
        ('auctions', '0013_userprofile_is_approved'),
        ('account', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Irreversible by design: there is no record of which addresses were
        # unverified beforehand, and un-verifying them all would re-create the
        # lockout this migration exists to undo.
        migrations.RunPython(verify_pre_existing_emails, migrations.RunPython.noop),
    ]
