"""Form-rendering helpers for the allauth element overrides.

allauth renders its forms through templates in ``allauth/elements/``, which the
project overrides to emit Bootstrap markup (see
auction_site/templates/allauth/elements/). Those overrides need to put CSS
classes on widgets that allauth's own forms declare, which a template cannot do
on its own.
"""

from django import template

register = template.Library()


@register.filter
def add_class(field, css_classes):
    """
    Render a bound form field with extra CSS classes on its widget.

    Preserves any classes the form already set rather than replacing them.
    """
    existing = field.field.widget.attrs.get('class', '')
    combined = f'{existing} {css_classes}'.strip()
    return field.as_widget(attrs={'class': combined})
