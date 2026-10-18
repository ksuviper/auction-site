/*
 * Service worker — deliberately the smallest one that does the job.
 *
 * Its two jobs are to make the site installable, which needs a fetch handler,
 * and to show something other than the browser's dinosaur when the phone has
 * no signal.
 *
 * It also receives push notifications (see the end of this file), for members
 * who have turned them on under Notifications. Push adds nothing to the cache.
 *
 * What it does NOT do is cache the site's pages. Listings, bids and invoices
 * change by the minute and are different for every signed-in member; serving
 * yesterday's copy of an auction from a cache would be worse than showing
 * nothing. So every request goes to the network, and only a single offline
 * page is stored.
 *
 * A service worker outlives the tab that installed it, so a mistake here is
 * hard to take back. That is the reason for the restraint.
 *
 * To remove it from a browser entirely: DevTools → Application → Service
 * Workers → Unregister, or ship a worker whose install handler calls
 * self.registration.unregister().
 */

const VERSION = '{{ version }}';
const CACHE_NAME = `asq-${VERSION}`;
const OFFLINE_URL = '{{ offline_url }}';
const ICON_URL = '{{ icon_url }}';
const DEFAULT_TITLE = '{{ default_title|escapejs }}';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' })))
      // Take over straight away rather than waiting for every tab to close,
      // so a fixed worker reaches people on their next page view.
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;

  // Only ever touch page loads. Anything else — form posts, the admin's
  // uploads, API calls, images — is left entirely alone, which is why a stale
  // or broken cache cannot corrupt a bid or a purchase.
  if (request.method !== 'GET' || request.mode !== 'navigate') {
    return;
  }

  event.respondWith(
    fetch(request).catch(() =>
      caches.match(OFFLINE_URL, { cacheName: CACHE_NAME }).then(
        (cached) =>
          cached ||
          new Response('You are offline.', {
            status: 503,
            headers: { 'Content-Type': 'text/plain' },
          }),
      ),
    ),
  );
});

// ── Push notifications ──────────────────────────────────────────────────────
//
// Purely additive: nothing above changes, and nothing here touches the cache.
// The server sends {title, body, url, tag}; every field is optional so a
// malformed payload still shows something rather than nothing.

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { body: event.data ? event.data.text() : '' };
  }

  const title = data.title || DEFAULT_TITLE;
  const options = {
    body: data.body || '',
    icon: ICON_URL,
    badge: ICON_URL,
    data: { url: data.url || '/' },
  };
  // Same tag replaces rather than stacks: three outbids on one plant are one
  // alert, updated, not three.
  if (data.tag) {
    options.tag = data.tag;
    options.renotify = true;
  }

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  // Resolved against this site, so a payload can only ever open one of our
  // own pages, and so it compares equal to an open tab's absolute URL.
  let target = new URL('/', self.location.origin).href;
  try {
    const url = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin);
    if (url.origin === self.location.origin) {
      target = url.href;
    }
  } catch (err) {
    // Keep the home page.
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === target && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(target);
      }
      return undefined;
    }),
  );
});
