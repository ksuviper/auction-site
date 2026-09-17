/*
 * Service worker — deliberately the smallest one that does the job.
 *
 * Its two jobs are to make the site installable, which needs a fetch handler,
 * and to show something other than the browser's dinosaur when the phone has
 * no signal.
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
