/*
 * Time-series line charts (reputation history, admin statistics) on Flot.
 * Colours come from the skin's CSS variables so charts follow dark mode (tick
 * labels are SVG text, styled by .flot-svg text in the CSS), and
 * the chart is redrawn when its container changes size.
 */
function osqaTimeChart(placeholder, data, options) {
    var $el = $(placeholder);
    if (!$el.length) return;
    var css = getComputedStyle(document.documentElement);
    function color(name, fallback) {
        return css.getPropertyValue(name).trim() || fallback;
    }
    var line = color('--brand', '#2c5aa0'), text = color('--muted', '#666'), grid = color('--border', '#ddd');
    var opts = $.extend(true, {
        colors: [line],
        xaxis: {mode: 'time', timeBase: 'milliseconds', color: grid},
        yaxis: {color: grid},
        grid: {borderWidth: 1, borderColor: grid, color: text, hoverable: true},
        series: {lines: {show: true, lineWidth: 2}, points: {show: false, radius: 2.5, fill: true, fillColor: line}}
    }, options);
    var series = [{data: data}];

    function draw() {
        if ($el.width() > 0 && $el.height() > 0) $.plot($el, series, opts);
    }
    draw();
    if (window.ResizeObserver) {
        var width = $el.width();
        new ResizeObserver(function () {
            if ($el.width() !== width) {
                width = $el.width();
                draw();
            }
        }).observe($el[0]);
    }
}
