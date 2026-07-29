from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

from .models import ListingComment, UserProfile
from .utils import _safe_send, needs_admin_approval

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=User)
def verify_staff_email_on_creation(sender, instance, created, **kwargs):
    """
    Give a newly created staff account a verified email address.

    ``createsuperuser`` predates allauth in the stack and leaves no EmailAddress
    row behind, so with ACCOUNT_EMAIL_VERIFICATION = 'mandatory' a fresh
    superuser cannot log in at /accounts/login/ and — being the person who would
    normally fix such things — has no one to ask. Migration 0014 repairs accounts
    that already existed; this keeps the next one from walking into it.

    There is nothing to verify in the first place: the address was typed by
    whoever had shell access (or by an existing admin adding a staff user), not
    submitted by an anonymous visitor. Normal sign-ups are untouched — they are
    created with is_staff False and go through allauth's confirmation flow.
    """
    if not created or not (instance.is_staff or instance.is_superuser):
        return
    if not instance.email:
        return

    # allauth's unique_verified_email constraint: one verified owner per address.
    already_claimed = (
        EmailAddress.objects.filter(email__iexact=instance.email, verified=True)
        .exclude(user=instance)
        .exists()
    )
    if already_claimed:
        return

    EmailAddress.objects.get_or_create(
        user=instance,
        email=instance.email,
        defaults={'verified': True, 'primary': True},
    )


@receiver(email_confirmed)
def notify_admin_of_pending_approval(request, email_address, **kwargs):
    """
    Tell the admin there is a new account to review.

    Fires the moment the user confirms their email — the last step they can take
    on their own, and the point at which they start waiting on us. Accounts that
    skip the approval gate (staff) raise nothing to review.
    """
    user = email_address.user
    if not needs_admin_approval(user):
        return

    admin_email = getattr(settings, 'ADMIN_EMAIL', '')
    if not admin_email:
        return

    review_link = reverse('admin:auctions_userprofile_changelist')
    if request is not None:
        review_link = request.build_absolute_uri(review_link)

    display_name = user.get_full_name() or user.get_username()
    body = f"""\
{display_name} ({user.email}) has verified their email address and is waiting
for approval before they can log in.

Review and approve pending accounts here:
{review_link}?is_approved__exact=0

-- ASQ Daylily Auction System
"""
    _safe_send(
        subject=f'New account pending approval: {user.email}',
        body=body,
        recipients=[admin_email],
    )


@receiver(pre_save, sender=UserProfile)
def _stash_prev_profile_approval(sender, instance, **kwargs):
    """Record the prior approval state so we can detect approve transitions."""
    if instance.pk:
        instance._prev_is_approved = bool(
            sender.objects.filter(pk=instance.pk)
            .values_list('is_approved', flat=True)
            .first()
        )
    else:
        instance._prev_is_approved = False


@receiver(post_save, sender=UserProfile)
def notify_user_of_approval(sender, instance, created, **kwargs):
    """
    Email the user when an admin approves their account.

    Keyed on the False -> True transition rather than living in the admin action,
    so approving inline from the changelist (``list_editable``) or from the
    profile detail page notifies the user too. Fires once per transition.

    Staff are skipped: they were never gated, so "your account is approved" would
    only be confusing. Bulk ``.update()`` calls do not emit post_save, which is
    why the 0013 backfill silently grandfathered existing members.
    """
    if not instance.is_approved or getattr(instance, '_prev_is_approved', False):
        return

    user = instance.user
    if user.is_staff or user.is_superuser or not user.email:
        return

    login_url = build_absolute_uri(None, reverse('account_login'))
    display_name = user.get_full_name() or user.get_username()
    body = f"""\
Hello {display_name},

Your ASQ Daylily Auctions account has been approved. You can now log in and
start using it.

Sign in here:
{login_url}

-- ASQ Daylily Auction Group
"""
    _safe_send(
        subject='Your ASQ Daylily Auctions account has been approved',
        body=body,
        recipients=[user.email],
    )


@receiver(pre_save, sender=ListingComment)
def _stash_prev_comment_approval(sender, instance, **kwargs):
    """Record the prior approval state so we can detect approve transitions."""
    if instance.pk:
        instance._prev_is_approved = bool(
            sender.objects.filter(pk=instance.pk)
            .values_list('is_approved', flat=True)
            .first()
        )
    else:
        instance._prev_is_approved = False


@receiver(post_save, sender=ListingComment)
def notify_comment_reply(sender, instance, created, **kwargs):
    """
    Email the original commenter when an admin/seller reply (a child comment)
    becomes approved. Fires once: on creation-as-approved or on the transition
    from unapproved to approved.
    """
    if not instance.parent_id or not instance.is_approved:
        return
    just_approved = created or not getattr(instance, '_prev_is_approved', False)
    if not just_approved:
        return

    parent = instance.parent
    recipient = parent.author.email
    if not recipient:
        return

    link = reverse('listing_detail', kwargs={'pk': instance.listing_id})
    body = f"""\
Your question on "{instance.listing.title}" has been answered.

Reply:
{instance.body}

View the listing:
{link}

-- ASQ Daylily Auction Group
"""
    _safe_send(
        subject=f'Your question on {instance.listing.title} has been answered',
        body=body,
        recipients=[recipient],
    )
