/* Reports JavaScript errors on the forum's pages to Sentry. The settings come
   from the #sentry-config element written by the {% sentry_browser %} tag. */
(function () {
    var element = document.getElementById('sentry-config');
    if (!element || !window.Sentry) {
        return;
    }
    var config = JSON.parse(element.textContent);

    // At most config.maxEvents reports per hour from this browser (kept in
    // localStorage, so reloading the page doesn't reset it), so a page that
    // fails in a loop can't send hundreds.
    var STORAGE_KEY = 'osqa-sentry-sent', HOUR = 3600 * 1000, sentInMemory = [];
    function allowReport() {
        var limit = config.maxEvents, now = Date.now(), sent;
        if (!limit) {
            return true;
        }
        try {
            sent = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
        } catch (e) {
            sent = sentInMemory;
        }
        sent = sent.filter(function (time) { return now - time < HOUR; });
        if (sent.length >= limit) {
            return false;
        }
        sent.push(now);
        sentInMemory = sent;
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sent));
        } catch (e) {
            // Private mode or storage full: the in-memory count still applies to this page.
        }
        return true;
    }

    window.Sentry.init({
        dsn: config.dsn,
        environment: config.environment,
        release: config.release || undefined,
        sendDefaultPii: false,
        // Only errors from the forum's own scripts and pages: browser
        // extensions and third-party widgets are not ours to fix.
        allowUrls: [window.location.origin],
        ignoreErrors: [
            'ResizeObserver loop limit exceeded',
            'ResizeObserver loop completed with undelivered notifications'
        ],
        beforeSend: function (event) {
            return allowReport() ? event : null;
        }
    });
    if (config.user) {
        window.Sentry.setUser({id: config.user});
    }
})();
