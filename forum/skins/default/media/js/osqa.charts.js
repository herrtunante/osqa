/*
 * Time-series line charts (reputation history, admin statistics) on Flot.
 * Colours come from the skin's CSS variables so charts follow dark mode (tick
 * labels are SVG text, styled by .flot-svg text in the CSS), and
 * the chart is redrawn when its container changes size.
 *
 * The time axis is laid out here rather than by Flot's time mode, so it is
 * always readable: the range covers at least a day (points close together
 * sit in the middle), ticks fall on whole hours, days, months or years in the
 * visitor's time zone, labels are dates (or hours within a day, never
 * seconds), and a narrow chart (a phone) gets fewer ticks.  Values on the
 * y axis are whole numbers.  The x values are epoch milliseconds.
 */
(function (window, $) {
    'use strict';

    var HOUR = 3600e3, DAY = 24 * HOUR, MIN_SPAN = DAY;
    // Candidate tick steps, smallest first: [unit, count, approximate ms].
    var STEPS = [
        ['hour', 1, HOUR], ['hour', 2, 2 * HOUR], ['hour', 3, 3 * HOUR], ['hour', 6, 6 * HOUR], ['hour', 8, 8 * HOUR],
        ['hour', 12, 12 * HOUR], ['day', 1, DAY], ['day', 2, 2 * DAY], ['day', 7, 7 * DAY],
        ['day', 14, 14 * DAY], ['month', 1, 30.4 * DAY], ['month', 2, 61 * DAY], ['month', 3, 91 * DAY],
        ['month', 6, 182 * DAY], ['year', 1, 365.25 * DAY], ['year', 2, 730.5 * DAY], ['year', 5, 1826 * DAY],
        ['year', 10, 3652 * DAY], ['year', 25, 9131 * DAY], ['year', 50, 18262 * DAY]
    ];

    function formatter(options) {
        var lang = document.documentElement.lang || undefined;
        try {
            return new Intl.DateTimeFormat(lang, options);
        } catch (e) {
            return new Intl.DateTimeFormat(undefined, options);
        }
    }

    // The x range of the data, widened to at least MIN_SPAN around its middle.
    function timeRange(data) {
        var min = Infinity, max = -Infinity;
        for (var i = 0; i < data.length; i++) {
            var x = +data[i][0];
            if (isFinite(x)) {
                if (x < min) min = x;
                if (x > max) max = x;
            }
        }
        if (min === Infinity) {
            max = Date.now();
            min = max - MIN_SPAN;
        }
        var span = max - min;
        if (span < MIN_SPAN) {
            var middle = min + span / 2;
            return {min: middle - MIN_SPAN / 2, max: middle + MIN_SPAN / 2};
        }
        var pad = span * 0.02;  // keep the first and last points off the frame
        return {min: min - pad, max: max + pad};
    }

    // First tick at or after `from` for a step of `count` units (local time).
    function firstTick(from, unit, count) {
        var d = new Date(from);
        d.setMinutes(0, 0, 0);
        if (unit === 'hour') {
            d.setHours(Math.floor(d.getHours() / count) * count);
        } else {
            d.setHours(0);
            if (unit === 'month' || unit === 'year') d.setDate(1);
            if (unit === 'month') d.setMonth(Math.floor(d.getMonth() / count) * count);
            if (unit === 'year') {
                d.setMonth(0);
                d.setFullYear(Math.floor(d.getFullYear() / count) * count);
            }
        }
        while (d.getTime() < from) advance(d, unit, count);
        return d;
    }

    function advance(d, unit, count) {
        if (unit === 'hour') d.setTime(d.getTime() + count * HOUR);
        else if (unit === 'day') d.setDate(d.getDate() + count);
        else if (unit === 'month') d.setMonth(d.getMonth() + count);
        else d.setFullYear(d.getFullYear() + count);
    }

    function timeTicks(range, width) {
        var most = Math.max(2, Math.min(8, Math.floor(width / 90)));
        var span = range.max - range.min, step = STEPS[STEPS.length - 1];
        for (var i = 0; i < STEPS.length; i++) {
            if (span / STEPS[i][2] <= most) {
                step = STEPS[i];
                break;
            }
        }
        var unit = step[0], count = step[1];
        var hour = formatter({hour: 'numeric', minute: '2-digit'});
        var day = formatter({month: 'short', day: 'numeric'});
        var dayYear = formatter({year: 'numeric', month: 'short', day: 'numeric'});
        var month = formatter({month: 'short'});
        var monthYear = formatter({year: 'numeric', month: 'short'});
        var year = formatter({year: 'numeric'});
        var manyYears = new Date(range.min).getFullYear() !== new Date(range.max).getFullYear();
        var ticks = [], previous = null;
        for (var d = firstTick(range.min, unit, count); d.getTime() <= range.max; advance(d, unit, count)) {
            var label;
            if (unit === 'hour') {
                // Hours within a day; the date at midnight (a range is at
                // least a day long, so one tick always names the date).
                label = d.getHours() === 0 ? day.format(d) : hour.format(d);
            } else if (unit === 'day') {
                label = manyYears && (previous === null || previous.getFullYear() !== d.getFullYear()) ? dayYear.format(d) : day.format(d);
            } else if (unit === 'month') {
                label = previous === null || d.getMonth() < count ? monthYear.format(d) : month.format(d);
            } else {
                label = year.format(d);
            }
            ticks.push([d.getTime(), label]);
            previous = new Date(d.getTime());
        }
        return ticks;
    }

    function osqaTimeChart(placeholder, data, options) {
        var $el = $(placeholder);
        if (!$el.length) return;
        data = data || [];
        // Read from the chart itself: it inherits the page's variables wherever they are set.
        var css = getComputedStyle($el[0]);
        function color(name, fallback) {
            return css.getPropertyValue(name).trim() || fallback;
        }
        // Pages without the skin's variables (the admin) use the page's text colour.
        var line = color('--brand', '#2c5aa0'), text = color('--muted', getComputedStyle($el[0]).color || '#666'),
            grid = color('--border', '#ddd');
        var range = timeRange(data);
        var base = $.extend(true, {
            colors: [line],
            xaxis: {color: grid, min: range.min, max: range.max, autoScale: 'none', showTickLabels: 'major'},
            yaxis: {color: grid, tickDecimals: 0, minTickSize: 1},
            grid: {borderWidth: 1, borderColor: grid, color: text, hoverable: true},
            series: {lines: {show: true, lineWidth: 2}, points: {show: false, radius: 2.5, fill: true, fillColor: line}}
        }, options);
        var series = [{data: data}];

        function draw() {
            var width = $el.width();
            if (width > 0 && $el.height() > 0) {
                var opts = $.extend(true, {}, base);
                opts.xaxis.ticks = timeTicks(range, width);
                // Flot measures only the labels between the first and last
                // tick, so two ticks (a phone) would get no room below.
                var font = parseFloat($el.css('font-size')) || 12, longest = 0;
                $.each(opts.xaxis.ticks, function (i, tick) { longest = Math.max(longest, tick[1].length); });
                if (!opts.xaxis.labelHeight) opts.xaxis.labelHeight = Math.max(14, Math.ceil(1.35 * font));
                if (!opts.xaxis.labelWidth) opts.xaxis.labelWidth = Math.ceil(0.6 * font * longest);
                $.plot($el, series, opts);
                $el.find('.flot-svg text').css('fill', text);
            }
        }
        draw();
        if (window.ResizeObserver) {
            // Width and height: a stylesheet applied after the first drawing
            // (a phone's shorter chart) would otherwise cut off the labels.
            var size = $el.width() + 'x' + $el.height();
            new ResizeObserver(function () {
                var now = $el.width() + 'x' + $el.height();
                if (now !== size) {
                    size = now;
                    draw();
                }
            }).observe($el[0]);
        }
    }

    window.osqaTimeChart = osqaTimeChart;
})(window, jQuery);
