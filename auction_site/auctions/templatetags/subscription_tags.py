"""Template tags that keep subscription logic out of templates."""

from django import template

from auctions.utils import has_active_subscription

register = template.Library()


@register.simple_tag
def user_can_bid(user):
    """Return True/False for whether ``user`` may bid or purchase."""
    return has_active_subscription(user)


@register.simple_tag
def user_subscription_status(user):
    """Return the user's subscription status string, or '' if none/anonymous."""
    if not getattr(user, 'is_authenticated', False):
        return ''
    subscription = getattr(user, 'subscription', None)
    return subscription.status if subscription is not None else ''
