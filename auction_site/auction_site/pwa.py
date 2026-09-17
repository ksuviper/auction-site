"""
The pieces a browser needs before it will offer to install the site.

Nothing here existed, which is the whole reason Chrome greyed out "Install" on
Android and showed no icon in the desktop address bar. A browser needs three
things and this module provides all three:

* **A manifest** naming the app, where it starts and how it should open.
* **Icons at 192 and 512 pixels**, as PNGs. Both sizes are required, and
  Android additionally wants a *maskable* pair it can crop to whatever shape
  the launcher uses.
* **A service worker with a fetch handler**, so the app does something sensible
  when the phone is offline.

It also needs **HTTPS**. That is a deployment matter, not a code one: on plain
HTTP a browser ignores all of this, and localhost is the only exception.

The icons are generated from whatever the admin uploaded under Site Content →
Branding, so the installed app matches the site rather than needing a second
set of files kept in step by hand.
"""

import io
import json
import logging

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import cache_control

from auctions.models import SiteSettings

logger = logging.getLogger(__name__)

# Bumping this retires every cached copy the moment the new worker activates.
# Change it whenever sw.js changes, or phones keep running the old one.
SERVICE_WORKER_VERSION = 'v1'

# The two sizes a browser insists on. Requests for anything else 404 rather
# than resize on demand: the size comes straight off the URL, and an open-ended
# one is an invitation to ask for a 20000-pixel PNG.
ICON_SIZES = (192, 512)

# Painted behind the icon. White rather than transparent so the result looks
# the same on a dark launcher as a light one, and so an uploaded image with a
# transparent background does not come out as a floating shape.
ICON_BACKGROUND = (255, 255, 255, 255)

# A maskable icon gets cropped to a circle, a squircle or a rounded square
# depending on the phone, so its content has to sit inside the middle. 60% of
# the width is the share that survives every shape.
MASKABLE_CONTENT_RATIO = 0.6

# Tints the browser's own chrome. Matches the site's dark navigation bar.
THEME_COLOUR = '#212529'
BACKGROUND_COLOUR = '#ffffff'

FALLBACK_NAME = 'Daylily Auctions'


def _settings_row():
    """The branding row, or an unsaved one carrying the defaults."""
    try:
        return SiteSettings.load()
    except Exception:
        logger.exception('Could not load SiteSettings for the PWA manifest')
        return SiteSettings()


def _source_image():
    """
    The picture the icons are cut from, as PNG bytes.

    The admin's app icon when there is one, otherwise the built-in logo. Both
    can fail — the upload may have been deleted from storage, the static file
    may be missing on a half-deployed server — and an icon that 500s takes
    installability with it, so the last resort is a blank square rather than an
    exception.
    """
    row = _settings_row()
    if row.app_icon_image:
        try:
            with row.app_icon_image.open('rb') as handle:
                return handle.read()
        except Exception:
            logger.exception('Could not read the uploaded app icon; using the logo')

    path = finders.find('img/logo.png')
    if path:
        try:
            with open(path, 'rb') as handle:
                return handle.read()
        except OSError:
            logger.exception('Could not read the built-in logo')
    return None


def _render_icon(size, maskable):
    """Fit the source onto a square canvas of the requested size."""
    from PIL import Image, ImageOps

    canvas = Image.new('RGBA', (size, size), ICON_BACKGROUND)

    raw = _source_image()
    if raw:
        try:
            source = Image.open(io.BytesIO(raw)).convert('RGBA')
            box = int(size * MASKABLE_CONTENT_RATIO) if maskable else size
            # contain() keeps the aspect ratio, so a wide logo is letterboxed
            # rather than squashed into a square.
            fitted = ImageOps.contain(source, (box, box), Image.LANCZOS)
            canvas.paste(
                fitted,
                ((size - fitted.width) // 2, (size - fitted.height) // 2),
                fitted,
            )
        except Exception:
            logger.exception('Could not process the icon image; serving a blank one')

    out = io.BytesIO()
    canvas.save(out, format='PNG', optimize=True)
    return out.getvalue()


def _icon_bytes(size, maskable):
    """
    Cached so a busy page does not re-encode a 512px PNG for every visitor.

    Keyed on the branding row's updated_at, so uploading a new icon in the
    admin publishes it immediately rather than after a cache timeout.
    """
    stamp = getattr(_settings_row(), 'updated_at', None)
    key = f'pwa-icon-{size}-{int(maskable)}-{stamp.timestamp() if stamp else 0}'

    data = cache.get(key)
    if data is None:
        data = _render_icon(size, maskable)
        cache.set(key, data, 60 * 60 * 24 * 30)
    return data


@cache_control(max_age=60 * 60 * 24, public=True)
def icon(request, size, maskable=False):
    if size not in ICON_SIZES:
        raise Http404('No icon of that size')
    return HttpResponse(_icon_bytes(size, maskable), content_type='image/png')


def maskable_icon(request, size):
    return icon(request, size, maskable=True)


@cache_control(max_age=60 * 60, public=True)
def manifest(request):
    """
    What the browser reads before offering to install.

    Built per request rather than stored as a static file because the name and
    the icons are admin-managed: renaming the site in the admin should rename
    the installed app, not leave a stale file to notice later.
    """
    row = _settings_row()
    name = row.site_name or FALLBACK_NAME
    slogan = row.site_slogan or ''
    full_name = f'{name} {slogan}'.strip() if slogan else name

    def icon_entry(size, purpose):
        url = (
            reverse('pwa_icon_maskable', args=[size])
            if purpose == 'maskable'
            else reverse('pwa_icon', args=[size])
        )
        return {
            'src': url,
            'sizes': f'{size}x{size}',
            'type': 'image/png',
            'purpose': purpose,
        }

    document = {
        # A stable id, so renaming the site updates the installed app rather
        # than looking like a different one.
        'id': '/',
        'name': full_name,
        # The site's own name, uncut. A launcher elides what will not fit and
        # does it on a word boundary; trimming to a recommended length here
        # just produced "Above Status".
        'short_name': name,
        'description': slogan or full_name,
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'any',
        'background_color': BACKGROUND_COLOUR,
        'theme_color': THEME_COLOUR,
        'icons': [
            icon_entry(size, purpose)
            for purpose in ('any', 'maskable')
            for size in ICON_SIZES
        ],
    }
    return HttpResponse(
        json.dumps(document, indent=2),
        content_type='application/manifest+json',
    )


def service_worker(request):
    """
    Served from the site root on purpose.

    A worker can only control URLs at or below its own path, so one served from
    /static/ would control /static/ and nothing else — and a worker that
    controls no pages does not make the site installable.
    """
    return render(
        request,
        'pwa/sw.js',
        {
            'version': SERVICE_WORKER_VERSION,
            'offline_url': reverse('pwa_offline'),
        },
        content_type='application/javascript',
    )


def offline(request):
    """
    Shown when a page is opened with no connection.

    Kept free of database queries and of anything from base.html that needs
    one: it is cached by the worker while online and rendered when nothing else
    is reachable, so whatever it needs has to already be in that cache.
    """
    return render(request, 'pwa/offline.html', {
        'site_name': _settings_row().site_name or FALLBACK_NAME,
        'debug': settings.DEBUG,
    })
