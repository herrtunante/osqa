"""Core Web Vitals measured in the e2e browser, on a throttled phone.

`measure(test, page, path)` loads `path` in a page opened with `phone_page()`
(390px phone, slow 4G: 150 ms round trip, 1.6 Mbit/s down, 750 kbit/s up;
CPU slowed down 4x through the DevTools protocol) and returns:

- ttfb: time to the first byte of the page (ms);
- fcp / lcp: first and largest contentful paint (ms), LCP read before the
  first interaction, which ends it; `lcp_element` names the element;
- cls: cumulative layout shift (largest session window, as Chrome reports it);
- inp: an INP proxy, the duration of the click on the page heading (event
  timing; the `first-input` entry when the click was faster than 16 ms);
- tbt: total blocking time of the long tasks until the page settled (ms);
- requests / bytes: requests made and bytes transferred (encoded, with
  headers) by the page itself, from the protocol's network events;
- third_party: requests to other sites (refused here, but counted).

Requests to other sites are refused (fulfilled with a tiny image for
Gravatar), so the figures depend only on the forum.

    E2E_VITALS_REPORT=file   append a Markdown table of the run to `file`
    E2E_VITALS_DEBUG=1       print each page's requests (bytes, URL) and layout shift sources
"""
import os
import time

# Slow 4G as Lighthouse defines it.
NETWORK = dict(offline=False, latency=150, downloadThroughput=1.6 * 1024 * 1024 / 8,
               uploadThroughput=750 * 1024 / 8)
CPU_SLOWDOWN = 4

OBSERVERS = '''(() => {
    const v = window.__vitals = {lcp: 0, lcpElement: '', fcp: 0, shifts: [], events: [], firstInput: null, longTasks: []};
    const describe = el => !el ? '' : el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
        (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
    const observe = (type, callback, extra) => {
        try { new PerformanceObserver(list => list.getEntries().forEach(callback)).observe(Object.assign({type, buffered: true}, extra || {})); }
        catch (e) { /* entry type not supported */ }
    };
    observe('largest-contentful-paint', e => { v.lcp = e.startTime; v.lcpElement = describe(e.element) + (e.url ? ' ' + e.url.split('/').pop().split('?')[0] : ''); });
    observe('paint', e => { if (e.name === 'first-contentful-paint') v.fcp = e.startTime; });
    observe('layout-shift', e => { if (!e.hadRecentInput) v.shifts.push({t: e.startTime, value: e.value,
        sources: (e.sources || []).map(s => describe(s.node)).filter(Boolean).slice(0, 3)}); });
    observe('event', e => v.events.push({name: e.name, duration: e.duration}), {durationThreshold: 16});
    observe('first-input', e => { v.firstInput = {name: e.name, duration: e.duration}; });
    observe('longtask', e => v.longTasks.push(e.duration));
})();'''

# Chrome's CLS: the largest "session window" (shifts less than 1 s apart, at most 5 s long).
READ = '''() => {
    const v = window.__vitals, nav = performance.getEntriesByType('navigation')[0];
    let best = 0, current = 0, start = -1, last = -1, sources = [], bestSources = [];
    for (const s of v.shifts) {
        if (start < 0 || s.t - last > 1000 || s.t - start > 5000) { start = s.t; current = 0; sources = []; }
        current += s.value; last = s.t; sources = sources.concat(s.sources);
        if (current > best) { best = current; bestSources = sources.slice(0, 5); }
    }
    return {ttfb: nav ? nav.responseStart : 0, fcp: v.fcp, lcp: v.lcp, lcp_element: v.lcpElement,
            cls: best, shift_sources: bestSources,
            tbt: v.longTasks.reduce((sum, d) => sum + Math.max(0, d - 50), 0)};
}'''

READ_INP = '''() => {
    const v = window.__vitals;
    const clicks = v.events.filter(e => ['pointerdown', 'pointerup', 'click', 'mousedown', 'mouseup'].includes(e.name));
    const longest = clicks.reduce((max, e) => Math.max(max, e.duration), 0);
    return longest || (v.firstInput ? v.firstInput.duration : 0);
}'''


def _grey_pixel():
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new('L', (1, 1), 200).save(out, 'PNG')
    return out.getvalue()


GREY_PIXEL = _grey_pixel()


def phone_page(test, user=None):
    """A phone page (light mode) with the network and CPU throttled."""
    page = test.open(user, device='mobile')
    page.set_default_timeout(60000)
    page.third_party = []
    base = test.live_server_url

    def route(route):
        url = route.request.url
        if url.startswith(base) or url.startswith('data:'):
            return route.continue_()
        page.third_party.append(url)
        if 'gravatar.com/' in url:
            return route.fulfill(status=200, content_type='image/png', body=GREY_PIXEL)
        return route.abort()
    page.context.route('**/*', route)
    page.add_init_script(OBSERVERS)

    cdp = page.context.new_cdp_session(page)
    cdp.send('Network.enable')
    cdp.send('Network.emulateNetworkConditions', NETWORK)
    cdp.send('Emulation.setCPUThrottlingRate', {'rate': CPU_SLOWDOWN})
    page.network = {'requests': {}, 'bytes': 0, 'sizes': {}}

    def sent(event):
        if not event['request']['url'].startswith('data:'):
            page.network['requests'][event['requestId']] = event['request']['url']

    def finished(event):
        if event['requestId'] in page.network['requests']:
            page.network['bytes'] += event.get('encodedDataLength', 0)
            page.network['sizes'][page.network['requests'][event['requestId']]] = event.get('encodedDataLength', 0)
    cdp.on('Network.requestWillBeSent', sent)
    cdp.on('Network.loadingFinished', finished)
    page.cdp = cdp
    return page


def measure(test, page, path, click='h1, .page-title, #main-content h2, main h2'):
    """Loads `path` in a page from phone_page() and returns its figures."""
    page.network['requests'].clear()
    page.network['bytes'] = 0
    page.network['sizes'].clear()
    del page.third_party[:]
    started = time.time()
    response = page.goto(test.url(path), wait_until='load')
    test.assertEqual(response.status, 200, path)
    # Late requests (deferred images, idle-time scripts) and paints.
    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    figures = page.evaluate(READ)
    figures.update(requests=len(page.network['requests']), bytes=page.network['bytes'],
                   third_party=len(page.third_party), path=path, seconds=round(time.time() - started, 1),
                   sizes=dict(page.network['sizes']))
    if os.environ.get('E2E_VITALS_DEBUG'):
        import sys
        sys.stderr.write('\n%s shifts=%s\n%s\n' % (path, figures['shift_sources'], '\n'.join(
            '%8d %s' % (size, url) for url, size in sorted(figures['sizes'].items(), key=lambda i: -i[1]))))
    target = page.locator(click).first
    if target.count():
        target.click(position={'x': 2, 'y': 2}, no_wait_after=True)
        page.wait_for_timeout(300)
        figures['inp'] = page.evaluate(READ_INP)
    else:
        figures['inp'] = 0
    test.check_page(page, path)
    return figures


COLUMNS = (('page', '%s'), ('ttfb', '%d'), ('fcp', '%d'), ('lcp', '%d'), ('cls', '%.3f'), ('inp', '%d'),
           ('tbt', '%d'), ('requests', '%d'), ('kb', '%.0f'), ('third_party', '%d'))


def table(results, title=''):
    lines = ['', title, '', '| ' + ' | '.join(c for c, _ in COLUMNS) + ' | LCP element |',
             '|' + '---|' * (len(COLUMNS) + 1)]
    for name, figures in results:
        values = dict(figures, page=name, kb=figures['bytes'] / 1024.0)
        lines.append('| ' + ' | '.join(fmt % values[c] for c, fmt in COLUMNS) + ' | %s |' % figures['lcp_element'])
    return '\n'.join(lines) + '\n'


def report(results, title):
    text = table(results, title)
    if os.environ.get('E2E_VITALS_REPORT'):
        with open(os.environ['E2E_VITALS_REPORT'], 'a') as f:
            f.write(text)
    return text
