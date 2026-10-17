/* Keeps the notification bell's unread count current: checks every minute
   while the page is visible, and when the tab comes back into view. */
(function () {
    var bell = document.getElementById('notification-bell');
    if (!bell || !window.fetch) {
        return;
    }
    var badge = bell.querySelector('.notification-count');
    var baseTitle = document.title.replace(/^\(\d+\) /, '');

    function show(count) {
        badge.textContent = count;
        badge.hidden = !count;
        bell.classList.toggle('has-unread', count > 0);
        bell.setAttribute('aria-label', count ? bell.dataset.labelSome.replace('%(count)s', count) : bell.dataset.labelNone);
        document.title = (count ? '(' + count + ') ' : '') + baseTitle;
    }

    function refresh() {
        if (document.visibilityState !== 'visible') {
            return;
        }
        fetch(bell.dataset.countUrl, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) { if (data) { show(data.unread); } })
            .catch(function () { /* offline: try again next time */ });
    }

    show(parseInt(badge.textContent, 10) || 0);
    setInterval(refresh, 60000);
    document.addEventListener('visibilitychange', refresh);
})();
