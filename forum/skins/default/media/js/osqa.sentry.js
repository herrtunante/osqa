/* Reports JavaScript errors on the forum's pages to Sentry. The settings come
   from the #sentry-config element written by the {% sentry_browser %} tag. */
(function () {
    var element = document.getElementById('sentry-config');
    if (!element || !window.Sentry) {
        return;
    }
    var config = JSON.parse(element.textContent);
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
        ]
    });
    if (config.user) {
        window.Sentry.setUser({id: config.user});
    }
})();
