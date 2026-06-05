"""Template tags that keep subscription logic out of templates."""

from django import template

from auctions.utils import has_active_subscription

register = template.Library()


@register.simple_tag
def user_can_bid(user):
    """Return True/False for whether ``user`` may bid or purchase."""
    return has_active_subscription(user)
