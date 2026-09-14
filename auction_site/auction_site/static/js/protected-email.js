/*
 * Turn the spans left by the protected_email tag back into mailto links.
 *
 * The server sends the address reversed and base64-encoded in data-pe, and a
 * spelled-out "name at example dot com" as the visible text, so the HTML a
 * harvester downloads contains no mailto: and nothing matching a user@host
 * pattern. This runs in the browser and puts the real link back.
 *
 * If anything here fails the page keeps the spelled-out text, which a visitor
 * can still read and retype. That is why the fallback is real text and not a
 * placeholder link.
 */
(function () {
  'use strict';

  function upgrade(span) {
    var packed = span.getAttribute('data-pe');
    if (!packed) {
      return;
    }

    var address;
    try {
      address = atob(packed).split('').reverse().join('');
    } catch (err) {
      return; // Leave the readable fallback in place.
    }

    var link = document.createElement('a');
    link.href = 'mailto:' + address;
    link.textContent = span.getAttribute('data-pe-label') || address;

    // Carry over any styling the surrounding page put on the span, minus our
    // own marker class.
    var classes = span.className
      .split(/\s+/)
      .filter(function (name) {
        return name && name !== 'protected-email';
      });
    if (classes.length) {
      link.className = classes.join(' ');
    }

    span.parentNode.replaceChild(link, span);
  }

  function run() {
    var spans = document.querySelectorAll('span.protected-email[data-pe]');
    Array.prototype.forEach.call(spans, upgrade);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
