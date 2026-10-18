/*
 * "Enable Notifications" on the notification settings page.
 *
 * Push is an extra channel on top of email. Every failure path here ends with
 * the same reassurance, because it is true: email keeps working whatever
 * happens on this page.
 *
 * Which state shows depends on things only the browser knows:
 *
 *   iPhone/iPad in a Safari tab
 *       Push only exists inside an installed (Home Screen) web app on iOS, and
 *       a Safari tab does not even expose PushManager. So this is checked
 *       FIRST — otherwise iPhone users would be told their browser cannot do
 *       notifications, when the real answer is "install the app first".
 *   No Push API at all
 *       Older browsers, and any page served over plain HTTP.
 *   Permission previously denied
 *       Only the browser's own settings can undo that; asking again is not
 *       allowed, so the page says where to look.
 *   Otherwise
 *       The Enable button, or "on for this device" if it already is.
 */
(function () {
  'use strict';

  var root = document.getElementById('push-settings');
  if (!root) {
    return;
  }

  var FAILURE =
    "Notifications weren't enabled. You can still rely on email updates for " +
    'outbid alerts and auction results.';
  var READY_TIMEOUT_MS = 10000;

  var vapidKey = root.dataset.vapidKey;
  var subscribeUrl = root.dataset.subscribeUrl;
  var unsubscribeUrl = root.dataset.unsubscribeUrl;
  var csrf = root.dataset.csrf;
  var messageBox = root.querySelector('[data-push-message]');

  var activeEndpoints = [];
  try {
    var script = document.getElementById('push-active-endpoints');
    activeEndpoints = JSON.parse(script ? script.textContent : '[]') || [];
  } catch (err) {
    activeEndpoints = [];
  }

  function showState(name) {
    var panels = root.querySelectorAll('[data-push-state]');
    for (var i = 0; i < panels.length; i++) {
      panels[i].hidden = panels[i].getAttribute('data-push-state') !== name;
    }
  }

  function say(text, kind) {
    if (!messageBox) {
      return;
    }
    messageBox.textContent = text;
    messageBox.className = 'alert mt-3 mb-0 alert-' + (kind || 'info');
    messageBox.hidden = !text;
  }

  function isStandalone() {
    var query = window.matchMedia && window.matchMedia('(display-mode: standalone)');
    return (query && query.matches) || window.navigator.standalone === true;
  }

  function isIOS() {
    var ua = window.navigator.userAgent;
    if (/iPad|iPhone|iPod/.test(ua)) {
      return true;
    }
    // iPadOS 13+ reports itself as a Mac; touch points still give it away.
    return window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1;
  }

  function supportsPush() {
    return (
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window
    );
  }

  // The applicationServerKey has to be raw bytes, not the base64url text the
  // server stores it as.
  function keyToBytes(base64url) {
    var padding = '='.repeat((4 - (base64url.length % 4)) % 4);
    var base64 = (base64url + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(base64);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) {
      bytes[i] = raw.charCodeAt(i);
    }
    return bytes;
  }

  // serviceWorker.ready never settles if the worker failed to register, so a
  // click would otherwise sit on "One moment…" forever.
  function workerReady() {
    return Promise.race([
      navigator.serviceWorker.ready,
      new Promise(function (resolve, reject) {
        window.setTimeout(function () {
          reject(new Error('Service worker not ready'));
        }, READY_TIMEOUT_MS);
      }),
    ]);
  }

  // Older Safari only takes a callback; newer browsers return a promise.
  function askPermission() {
    return new Promise(function (resolve) {
      var result = Notification.requestPermission(resolve);
      if (result && typeof result.then === 'function') {
        result.then(resolve);
      }
    });
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(payload),
    }).then(function (response) {
      if (!response.ok) {
        throw new Error('Server answered ' + response.status);
      }
      return response.json();
    });
  }

  function subscribe(registration) {
    var options = { userVisibleOnly: true, applicationServerKey: keyToBytes(vapidKey) };
    return registration.pushManager.getSubscription().then(function (existing) {
      if (!existing) {
        return registration.pushManager.subscribe(options);
      }
      // Reuse what the browser already holds. If it was made with a different
      // key (the site's keys were replaced), subscribe() would refuse, so
      // drop it and start again.
      return registration.pushManager.subscribe(options).catch(function () {
        return existing.unsubscribe().then(function () {
          return registration.pushManager.subscribe(options);
        });
      });
    });
  }

  function enable() {
    say('');
    // Must be the first thing the click does: Safari only lets a page ask for
    // permission directly in response to a tap.
    askPermission()
      .then(function (permission) {
        if (permission !== 'granted') {
          showState(Notification.permission === 'denied' ? 'denied' : 'enable');
          say(FAILURE, 'warning');
          return null;
        }
        showState('busy');
        return workerReady()
          .then(subscribe)
          .then(function (subscription) {
            return post(subscribeUrl, subscription.toJSON());
          })
          .then(function () {
            showState('enabled');
            say("Done. You'll get alerts on this device as well as by email.", 'success');
          });
      })
      .catch(function () {
        showState('enable');
        say(FAILURE, 'warning');
      });
  }

  function disable() {
    say('');
    showState('busy');
    workerReady()
      .then(function (registration) {
        return registration.pushManager.getSubscription();
      })
      .then(function (subscription) {
        if (!subscription) {
          return null;
        }
        // Tell the server first, so it stops sending even if the browser's own
        // unsubscribe fails.
        return post(unsubscribeUrl, { endpoint: subscription.endpoint }).then(function () {
          return subscription.unsubscribe();
        });
      })
      .then(function () {
        showState('enable');
        say('Notifications are off on this device. Email updates continue as before.', 'info');
      })
      .catch(function () {
        showState('enabled');
        say("That didn't work. Please try again in a moment.", 'warning');
      });
  }

  function wire() {
    var enableButton = root.querySelector('[data-push-action="enable"]');
    var disableButton = root.querySelector('[data-push-action="disable"]');
    if (enableButton) {
      enableButton.addEventListener('click', enable);
    }
    if (disableButton) {
      disableButton.addEventListener('click', disable);
    }
  }

  function initialState() {
    if (isIOS() && !isStandalone()) {
      showState('ios-install');
      return;
    }
    if (!supportsPush() || !vapidKey) {
      showState('unsupported');
      return;
    }
    if (Notification.permission === 'denied') {
      showState('denied');
      return;
    }
    wire();
    workerReady()
      .then(function (registration) {
        return registration.pushManager.getSubscription();
      })
      .then(function (subscription) {
        var known = subscription && activeEndpoints.indexOf(subscription.endpoint) !== -1;
        showState(known && Notification.permission === 'granted' ? 'enabled' : 'enable');
      })
      .catch(function () {
        // No worker yet (first visit, still installing). The button retries.
        showState('enable');
      });
  }

  initialState();
})();
