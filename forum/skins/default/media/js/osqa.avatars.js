/*
 * Avatars that can't be loaded (Gravatar blocked or unreachable) show the
 * first letter of the name (as the people in the search suggestions do)
 * instead of the browser's broken-image icon.
 *
 * Error events don't bubble, so they are caught on the document in the
 * capture phase; images that failed before this deferred script ran are
 * found by their state. The letter comes from the alt text (the user name)
 * and its colours from the theme (--muted, --surface: visible on any card),
 * redrawn when the colour scheme changes.
 */
(function () {
    'use strict';

    var SELECTOR = 'img.gravatar';
    var broken = [];

    function colours() {
        var style = getComputedStyle(document.documentElement);
        return {
            background: (style.getPropertyValue('--muted') || '#5f6b7a').trim(),
            text: (style.getPropertyValue('--surface') || '#ffffff').trim()
        };
    }

    function escapeXml(text) {
        return text.replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'}[c];
        });
    }

    function letterImage(name) {
        var letter = (Array.from((name || '').trim())[0] || '?').toUpperCase();
        var c = colours();
        var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
            '<rect width="64" height="64" fill="' + escapeXml(c.background) + '"/>' +
            '<text x="32" y="32" dy=".35em" text-anchor="middle" fill="' + escapeXml(c.text) + '" ' +
            'font-family="system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif" font-size="30" font-weight="700">' +
            escapeXml(letter) + '</text></svg>';
        return 'data:image/svg+xml,' + encodeURIComponent(svg);
    }

    function useFallback(img) {
        if (!img.dataset.avatarSrc) {
            img.dataset.avatarSrc = img.getAttribute('src') || '';
            broken.push(img);
        }
        img.classList.add('gravatar-fallback');
        img.removeAttribute('srcset');
        img.src = letterImage(img.getAttribute('alt'));
    }

    document.addEventListener('error', function (event) {
        var img = event.target;
        if (img && img.matches && img.matches(SELECTOR) && !img.classList.contains('gravatar-fallback')) {
            useFallback(img);
        }
    }, true);

    function scan(root) {
        (root || document).querySelectorAll(SELECTOR + ':not(.gravatar-fallback)').forEach(function (img) {
            if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) {
                useFallback(img);
            }
        });
    }

    scan();
    if (window.matchMedia) {
        var scheme = window.matchMedia('(prefers-color-scheme: dark)');
        var redraw = function () {
            broken = broken.filter(function (img) { return img.isConnected; });
            broken.forEach(function (img) { img.src = letterImage(img.getAttribute('alt')); });
        };
        if (scheme.addEventListener) {
            scheme.addEventListener('change', redraw);
        }
    }
    window.osqaAvatars = {scan: scan};
})();
