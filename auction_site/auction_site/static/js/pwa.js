/*
 * Register the service worker, which is what makes the site installable.
 *
 * Browsers only accept a worker over HTTPS, with localhost as the single
 * exception for development. On plain HTTP the registration below rejects and
 * the install option stays hidden — that is the browser's rule, not something
 * this file can work around, so a failure is logged rather than shown to a
 * visitor. Nothing on the site depends on the worker: it adds an offline page
 * and the install prompt, and the site works normally without it.
 */
(function () {
  'use strict';

  if (!('serviceWorker' in navigator)) {
    return;
  }

  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function (err) {
      // Most often: the page is on http://, or an earlier worker is still
      // shutting down. Neither is worth interrupting anyone over.
      if (window.console && console.info) {
        console.info('Service worker not registered:', err && err.message);
      }
    });
  });
})();
