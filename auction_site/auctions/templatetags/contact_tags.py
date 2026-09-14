"""
Publish a contact address without leaving one a harvester can pattern-match.

Address-harvesting bots overwhelmingly work by pattern rather than by
understanding a page: they pull the address out of ``href="mailto:..."`` and
run a ``user@host`` regular expression over the response body. Neither pattern
survives this module. The address is reversed and base64-encoded into a data
attribute, and the text a visitor sees before JavaScript runs spells out "at"
and "dot", so the served HTML contains no substring either technique matches.
JavaScript then rebuilds a real ``mailto:`` link in the DOM.

What this does not do: stop a scraper that executes JavaScript, or one that
understands "name at example dot com". Those exist, they are a small minority
of harvesting traffic, and the only complete defence is never publishing the
address at all — a contact form that posts to the server. Treat this as
raising the cost, not as a guarantee.

Nothing here changes what is stored. The database and the settings keep the
real address; this is a presentation concern only, so admin editing, outgoing
mail and anything else that needs the address are unaffected.

Two entry points, because a page has two sources of addresses:

``{% protected_email %}``
    For an address written into a template. Defaults to settings.CONTACT_EMAIL.

``{{ html|protect_emails }}``
    For admin-authored HTML (FAQ answers, site page bodies), where an address
    may be typed as bare text or as a mailto link and cannot be caught at
    author time.
"""

import base64
import re

from django import template
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


# Deliberately not a validating pattern — it is a *finder* run over prose an
# admin typed, so it errs toward the common shapes and leaves anything odd
# alone rather than mangling it.
EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)

# An admin who used the editor's link button produces this. Matched before the
# bare-address pass so the whole element is replaced as a unit; rewriting only
# the text inside would leave the address sitting in the href.
#
# Regular expressions are the wrong tool for general HTML, but this runs over
# short admin-authored prose, and the alternative — parsing and re-serialising
# every page body — risks changing markup this project deliberately passes
# through untouched. Anything the pattern does not match is simply left alone.
MAILTO_ANCHOR_RE = re.compile(
    r'<a\b[^>]*?href\s*=\s*(["\'])\s*mailto:(?P<address>[^"\'?\s]+)[^"\']*\1'
    r'[^>]*>(?P<label>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

TAG_RE = re.compile(r'<[^>]+>')


def pack(address):
    """
    Reverse then base64 the address.

    Reversing first is not encryption and is not pretending to be; it means the
    obvious "try base64-decoding every attribute" sweep produces a string that
    still is not an address, which is enough to fall out of a bulk harvester's
    pipeline.
    """
    return base64.b64encode(address[::-1].encode('utf-8')).decode('ascii')


def unpack(packed):
    """Inverse of pack(). Used by the tests, and by anyone debugging a page."""
    return base64.b64decode(packed.encode('ascii')).decode('utf-8')[::-1]


def spell_out(address):
    """
    The address as a human reads it aloud, for the no-JavaScript fallback.

    Only the final dot pair matters to a regular expression, but every dot is
    spelled out so the result reads consistently.
    """
    local, _, domain = address.partition('@')
    return f"{local} at {domain.replace('.', ' dot ')}"


def protected_markup(address, label=None):
    """
    The span that JavaScript upgrades into a mailto link.

    A span rather than an anchor with a placeholder href: without JavaScript a
    dead link that looks live is worse than plain text, because a visitor
    clicks it and nothing happens. This way the fallback is honest — readable
    text they can retype.
    """
    attrs = [
        'class="protected-email"',
        f'data-pe="{escape(pack(address))}"',
    ]
    if label:
        attrs.append(f'data-pe-label="{escape(label)}"')
    visible = escape(label) if label else escape(spell_out(address))
    return mark_safe(f'<span {" ".join(attrs)}>{visible}</span>')


@register.simple_tag
def protected_email(address=None, label=None):
    """
    Render a contact address that the page source does not give away.

    Usage:

        {% protected_email %}                       site contact address
        {% protected_email 'help@example.com' %}    a specific address
        {% protected_email label='Email us' %}      custom link text
    """
    return protected_markup(address or settings.CONTACT_EMAIL, label)


@register.filter(is_safe=True)
def protect_emails(html):
    """
    Rewrite every address in a block of admin-authored HTML.

    Returns safe HTML, so it replaces ``|safe`` at the call site rather than
    being chained after it: the point is to pass the admin's markup through
    while rewriting the addresses inside it.
    """
    if not html:
        return ''

    text = str(html)

    def replace_anchor(match):
        address = match.group('address')
        # An anchor whose text is just the address again carries no label worth
        # keeping — show the spelled-out fallback instead. One that says
        # something ("email the club") keeps its wording.
        label = TAG_RE.sub('', match.group('label')).strip()
        if not label or label.casefold() == address.casefold():
            label = None
        return protected_markup(address, label)

    text = MAILTO_ANCHOR_RE.sub(replace_anchor, text)
    # Then any address the admin typed as plain text.
    text = EMAIL_RE.sub(lambda m: protected_markup(m.group(0)), text)
    return mark_safe(text)
