/*
 * Tab rows that scroll sideways on narrow screens (header tabs, sort tabs,
 * profile tabs, settings sections): the edge where more tabs are hidden
 * fades out (classes fade-start / fade-end, see osqa.css "Scrolling tab
 * rows"), and the current tab is scrolled into view when the page loads.
 */
(function () {
    'use strict';

    var ROWS = '#nav .nav-tabs, .tabsA, .settings-nav ul';
    var CURRENT = '.on, [aria-current]:not([aria-current="false"])';

    function scrolls(row) {
        return /(auto|scroll)/.test(getComputedStyle(row).overflowX);
    }

    function update(row) {
        var max = row.scrollWidth - row.clientWidth;
        var active = max > 1 && scrolls(row);
        // Right-to-left rows count scrollLeft from 0 down to -max.
        var position = Math.abs(row.scrollLeft);
        var rtl = getComputedStyle(row).direction === 'rtl';
        var start = active && position > 1, end = active && position < max - 1;
        row.classList.toggle('scroll-row', active);
        row.classList.toggle(rtl ? 'fade-end' : 'fade-start', start);
        row.classList.toggle(rtl ? 'fade-start' : 'fade-end', end);
    }

    function showCurrent(row) {
        var current = row.querySelector(CURRENT);
        if (!current || row.scrollWidth <= row.clientWidth + 1 || !scrolls(row)) {
            return;
        }
        var box = row.getBoundingClientRect(), tab = current.getBoundingClientRect();
        var fade = 32;
        if (tab.left < box.left + fade || tab.right > box.right - fade) {
            // Centre it, without scrolling the page itself.
            row.scrollLeft += (tab.left + tab.width / 2) - (box.left + box.width / 2);
        }
    }

    function setUp(row) {
        if (row.dataset.scrollTabs) {
            return;
        }
        row.dataset.scrollTabs = '1';
        showCurrent(row);
        update(row);
        row.addEventListener('scroll', function () { update(row); }, {passive: true});
        if (window.ResizeObserver) {
            new ResizeObserver(function () { update(row); }).observe(row);
        }
    }

    function init() {
        document.querySelectorAll(ROWS).forEach(setUp);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.addEventListener('resize', function () {
        document.querySelectorAll(ROWS).forEach(update);
    });
    window.osqaScrollTabs = {init: init};
})();
