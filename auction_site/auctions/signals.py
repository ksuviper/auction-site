from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

from .models import ListingComment, UserProfile
from .utils import _safe_send

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


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
