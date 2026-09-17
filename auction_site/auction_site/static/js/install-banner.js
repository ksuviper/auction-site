/*
 * The "install this app" bar.
 *
 * The browser's own install affordance is easy to miss — an icon in the desktop
 * address bar, a menu entry on Android — and on iOS there is none at all. This
 * offers it where people will see it.
 *
 * The two platforms are genuinely different and the bar is not the same thing
 * on each:
 *
 *   Chrome, Edge, most Android browsers
 *       fire 'beforeinstallprompt'. Holding on to that event lets a button
 *       replay the browser's real install dialog later. The bar only appears
 *       once the event has actually fired, so the button is never dead.
 *
 *   iOS Safari
 *       has never fired that event and Apple provides no way to trigger an
 *       install from code. The only route is Share → Add to Home Screen, so
 *       here the bar can only explain. That makes it the more important of the
 *       two: without it an iPhone user has no way to discover the app exists.
 *
 * Nothing here is load-bearing. With JavaScript off, or if any of this fails,
 * the site behaves exactly as it always has and the browser's own install
 * affordance still works.
 */
(function () {
  'use strict';

  var DISMISSED_KEY = 'pwaBannerDismissedAt';
  var DISMISSED_DAYS = 14;

  var banner = document.getElementById('pwa-install-banner');
  if (!banner) {
    return;
  }

  var installButton = banner.querySelector('[data-pwa-action="install"]');
  var dismissButton = banner.querySelector('[data-pwa-action="dismiss"]');
  var icon = banner.querySelector('[data-pwa-icon]');
  var deferredPrompt = null;

  // localStorage throws in a private window and can be disabled outright, and
  // a banner is not worth breaking a page over.
  function readDismissedAt() {
    try {
      return parseInt(window.localStorage.getItem(DISMISSED_KEY), 10) || 0;
    } catch (err) {
      return 0;
    }
  }

  function rememberDismissal() {
    try {
      window.localStorage.setItem(DISMISSED_KEY, String(Date.now()));
    } catch (err) {
      /* Then it reappears next visit. Annoying, not broken. */
    }
  }

  function dismissedRecently() {
    var at = readDismissedAt();
    if (!at) {
      return false;
    }
    var days = (Date.now() - at) / 86400000;
    return days < DISMISSED_DAYS;
  }

  function isStandalone() {
    // The first covers every browser that implements the display-mode query;
    // navigator.standalone is the older iOS-only flag, still what Safari sets
    // for an app opened from the home screen.
    var query = window.matchMedia && window.matchMedia('(display-mode: standalone)');
    return (query && query.matches) || window.navigator.standalone === true;
  }

  function isIOS() {
    var ua = window.navigator.userAgent;
    if (/iPad|iPhone|iPod/.test(ua)) {
      return true;
    }
    // An iPad on iPadOS 13+ reports itself as a Mac. The touch-point count is
    // what still separates it from a real desktop.
    return (
      window.navigator.platform === 'MacIntel' &&
      window.navigator.maxTouchPoints > 1
    );
  }

  function show(which) {
    var message = banner.querySelector('[data-pwa-message="' + which + '"]');
    if (message) {
      message.hidden = false;
    }
    if (icon && icon.dataset.pwaIcon) {
      // Set here rather than in the markup so the icon is fetched only for the
      // few page views that actually show the bar.
      icon.src = icon.dataset.pwaIcon;
    }
    banner.hidden = false;
  }

  function hide() {
    banner.hidden = true;
  }

  if (isStandalone() || dismissedRecently()) {
    return;
  }

  if (dismissButton) {
    dismissButton.addEventListener('click', function () {
      rememberDismissal();
      hide();
    });
  }

  if (isIOS()) {
    // Instructions only. There is no API to wire a button to.
    show('ios');
  } else {
    window.addEventListener('beforeinstallprompt', function (event) {
      // Stops the browser showing its own mini-infobar, so there is one prompt
      // rather than two competing ones.
      event.preventDefault();
      deferredPrompt = event;
      if (installButton) {
        installButton.hidden = false;
      }
      show('prompt');
    });
  }

  if (installButton) {
    installButton.addEventListener('click', function () {
      if (!deferredPrompt) {
        hide();
        return;
      }
      deferredPrompt.prompt();
      // Either way the bar has done its job: they have seen the real dialog
      // and answered it. A dismissal is not recorded on "no" — the browser
      // will not re-offer soon anyway, and treating a declined dialog as a
      // 14-day dismissal would be two refusals for one click.
      var choice = deferredPrompt.userChoice;
      if (choice && typeof choice.then === 'function') {
        choice.then(function () {
          deferredPrompt = null;
          hide();
        });
      } else {
        deferredPrompt = null;
        hide();
      }
    });
  }

  window.addEventListener('appinstalled', function () {
    deferredPrompt = null;
    hide();
  });
})();
