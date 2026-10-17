"""Prometheus metrics (text exposition format 0.0.4), without a client library.

gunicorn runs several worker processes, and a scrape reaches only one of
them, so counters are kept in the shared Django cache (memcached in
production), not in process memory:

- Request counts and latencies are added to a per-process buffer (no I/O on
  the request path) and flushed to the cache with `cache.incr` every few
  seconds by a background thread, so the totals lag by at most FLUSH_SECONDS.
- Other counters (emails, security events) are rare and go to the cache at
  once, so short-lived cron processes don't lose them.
- Figures read from the database (users, posts, moderation queue, cron runs)
  are computed at scrape time and cached for GAUGE_SECONDS.

Counters start again from zero when the cache is cleared or memcached
restarts; Prometheus' rate() and increase() handle such resets. With the file
cache, increments from different processes can occasionally be lost.
"""
import logging
import os
import threading
import time

from django.core.cache import cache

log = logging.getLogger(__name__)

PREFIX = 'osqa-metrics:'
INDEX_KEY = PREFIX + 'index'
GAUGES_KEY = PREFIX + 'gauges'
FLUSH_SECONDS = 5
GAUGE_SECONDS = 60

# Request duration histogram buckets, in seconds.
BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HELP = {
    'osqa_http_requests_total': ('counter', 'HTTP requests by view name, method and status class.'),
    'osqa_http_request_duration_seconds': ('histogram', 'Time to answer HTTP requests, by view name.'),
    'osqa_emails_sent_total': ('counter', 'Emails accepted by the SMTP server.'),
    'osqa_emails_failed_total': ('counter', 'Emails that could not be sent.'),
    'osqa_security_events_total': ('counter', 'Security log events (login_failed, login_paused = locked out, ...).'),
}


def _key(name, labels):
    return PREFIX + name + '|' + '|'.join('%s=%s' % kv for kv in labels)


def _labels(labels):
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


# --- writing -----------------------------------------------------------------

def _incr(key, amount):
    try:
        cache.incr(key, amount)
    except ValueError:  # not in the cache yet
        if not cache.add(key, amount, timeout=None):
            cache.incr(key, amount)


def _register(series):
    """Adds (name, labels) pairs to the index the scrape reads."""
    index = cache.get(INDEX_KEY) or set()
    if not series <= index:
        cache.set(INDEX_KEY, index | series, timeout=None)


def count(name, amount=1, **labels):
    """Adds to a counter at once (for rare events). Never raises."""
    try:
        series = (name, _labels(labels))
        _incr(_key(*series), amount)
        _register({series})
    except Exception:
        log.debug('metrics: could not count %s', name, exc_info=True)


class _Buffer:
    """Per-process request statistics, flushed to the cache periodically."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.known = set()
        self.pid = None
        self.thread = None

    def add(self, items):
        with self.lock:
            for series, amount in items:
                self.values[series] = self.values.get(series, 0) + amount
            self._ensure_thread()

    def _ensure_thread(self):
        # After a fork (gunicorn preload) the parent's thread doesn't exist in the child.
        if self.pid != os.getpid() or self.thread is None or not self.thread.is_alive():
            self.pid = os.getpid()
            self.thread = threading.Thread(target=self._run, name='osqa-metrics-flush', daemon=True)
            self.thread.start()

    def _run(self):
        while True:
            time.sleep(FLUSH_SECONDS)
            self.flush()

    def flush(self):
        with self.lock:
            values, self.values = self.values, {}
        if not values:
            return
        try:
            for series, amount in values.items():
                _incr(_key(*series), amount)
            # Re-checked on every flush, so a series lost in a race between
            # two workers updating the index at once comes back.
            self.known |= set(values)
            _register(self.known)
        except Exception:
            log.warning('metrics: flushing request statistics to the cache failed', exc_info=True)


_buffer = _Buffer()


def observe_request(view, method, status, seconds):
    """Records one HTTP request (no I/O; flushed in the background)."""
    status_class = '%dxx' % (int(status) // 100)
    method = method if method in ('GET', 'POST', 'HEAD', 'PUT', 'DELETE', 'PATCH', 'OPTIONS') else 'other'
    labels = (('method', method), ('status', status_class), ('view', view))
    bucket = next((str(b) for b in BUCKETS if seconds <= b), '+Inf')
    _buffer.add([
        (('osqa_http_requests_total', labels), 1),
        (('osqa_http_request_duration_seconds_bucket', (('le', bucket), ('view', view))), 1),
        (('osqa_http_request_duration_seconds_sum_ms', (('view', view),)), int(round(seconds * 1000))),
    ])


def flush():
    _buffer.flush()


# --- reading -----------------------------------------------------------------

def _escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def _fmt_labels(labels):
    if not labels:
        return ''
    return '{%s}' % ','.join('%s="%s"' % (k, _escape(v)) for k, v in labels)


def _fmt_value(value):
    if isinstance(value, float):
        return repr(value) if value == value else 'NaN'
    return str(int(value))


def _counter_lines():
    """Counters and histograms stored in the cache."""
    index = cache.get(INDEX_KEY) or set()
    if not index:
        return {}
    keys = {series: _key(*series) for series in index}
    values = cache.get_many(list(keys.values()))
    out = {}  # metric name -> list of (labels, value)

    histograms = {}
    for (name, labels), key in sorted(keys.items()):
        value = values.get(key) or 0
        if name == 'osqa_http_request_duration_seconds_bucket':
            le = dict(labels)['le']
            view = dict(labels)['view']
            histograms.setdefault(view, {'buckets': {}, 'sum': 0})['buckets'][le] = value
        elif name == 'osqa_http_request_duration_seconds_sum_ms':
            histograms.setdefault(dict(labels)['view'], {'buckets': {}, 'sum': 0})['sum'] = value
        else:
            out.setdefault(name, []).append((labels, value))

    lines = []
    for view, data in sorted(histograms.items()):
        cumulative = 0
        for le in [str(b) for b in BUCKETS] + ['+Inf']:
            cumulative += data['buckets'].get(le, 0)
            lines.append(((('le', le), ('view', view)), cumulative, '_bucket'))
        lines.append(((('view', view),), data['sum'] / 1000.0, '_sum'))
        lines.append(((('view', view),), cumulative, '_count'))
    if lines:
        out['osqa_http_request_duration_seconds'] = lines
    return out


def _cache_stats():
    """memcached's own hit/miss counters, when the cache is pymemcache."""
    clients = getattr(getattr(cache, '_cache', None), 'clients', None)
    if not clients or not hasattr(cache, '_servers'):
        return {}
    totals = {}
    for client in list(clients.values()):
        try:
            stats = client.stats()
        except Exception:
            continue
        for name in (b'get_hits', b'get_misses', b'evictions', b'curr_items', b'bytes', b'limit_maxbytes'):
            if name in stats:
                totals[name.decode()] = totals.get(name.decode(), 0) + int(stats[name])
    return totals


def database_gauges():
    """Figures from the database, cached for GAUGE_SECONDS for all workers."""
    cached = cache.get(GAUGES_KEY)
    if cached is not None:
        return cached
    from django.db.models import Count, Exists, OuterRef
    from forum.models import Flag, ModerationReport, Node, Question, User
    from forum.utils import cron

    gauges = {}
    gauges['osqa_users'] = [((), User.objects.count())]
    gauges['osqa_users_active'] = [((), User.objects.filter(is_active=True).count())]
    by_type = dict(Node.objects.exclude(state_string__contains='(deleted)').order_by()
                   .values_list('node_type').annotate(n=Count('id')))
    gauges['osqa_posts'] = [((('type', t),), by_type.get(t, 0)) for t in ('question', 'answer', 'comment')]
    answers = Node.objects.filter(node_type='answer', parent_id=OuterRef('pk')) \
        .exclude(state_string__contains='(deleted)')
    gauges['osqa_questions_unanswered'] = [((), Question.objects.exclude(state_string__contains='(deleted)')
                                            .filter(~Exists(answers)).count())]
    gauges['osqa_moderation_pending'] = [
        ((('kind', 'flag'),), Flag.objects.filter(action__canceled=False)
         .exclude(node__state_string__contains='(deleted)').count()),
        ((('kind', 'report'),), ModerationReport.objects.filter(resolved_at__isnull=True).count()),
    ]
    runs = cron.last_runs()
    gauges['osqa_cron_last_success_timestamp_seconds'] = [
        ((('command', c),), r['success']) for c, r in sorted(runs.items()) if r.get('success')]
    gauges['osqa_cron_last_failure_timestamp_seconds'] = [
        ((('command', c),), r['failure']) for c, r in sorted(runs.items()) if r.get('failure')]
    gauges['osqa_cron_last_duration_seconds'] = [
        ((('command', c),), float(r.get('duration', 0))) for c, r in sorted(runs.items()) if 'duration' in r]
    cache.set(GAUGES_KEY, gauges, GAUGE_SECONDS)
    return gauges


GAUGE_HELP = {
    'osqa_users': 'Registered accounts.',
    'osqa_users_active': 'Accounts not deactivated.',
    'osqa_posts': 'Posts that are not deleted, by type.',
    'osqa_questions_unanswered': 'Questions (not deleted) without any answer.',
    'osqa_moderation_pending': 'Items waiting for moderators: user flags and automated reports not resolved.',
    'osqa_cron_last_success_timestamp_seconds': 'Unix time the scheduled command last finished without error.',
    'osqa_cron_last_failure_timestamp_seconds': 'Unix time the scheduled command last failed.',
    'osqa_cron_last_duration_seconds': 'How long the last run of the scheduled command took.',
    'osqa_cache_get_hits_total': 'memcached get hits (all servers).',
    'osqa_cache_get_misses_total': 'memcached get misses (all servers).',
    'osqa_cache_evictions_total': 'memcached evictions (all servers).',
    'osqa_cache_items': 'Items in memcached.',
    'osqa_cache_bytes': 'Bytes used in memcached.',
    'osqa_cache_limit_bytes': 'memcached memory limit.',
}


def render():
    """The whole exposition text."""
    flush()  # this worker's own requests are included at once
    blocks = []

    def block(name, kind, help_text, samples, suffix_mode=False):
        if not samples:
            return
        blocks.append('# HELP %s %s' % (name, help_text))
        blocks.append('# TYPE %s %s' % (name, kind))
        for sample in samples:
            if suffix_mode:
                labels, value, suffix = sample
                blocks.append('%s%s%s %s' % (name, suffix, _fmt_labels(labels), _fmt_value(value)))
            else:
                labels, value = sample
                blocks.append('%s%s %s' % (name, _fmt_labels(labels), _fmt_value(value)))

    counters = _counter_lines()
    for name in sorted(counters):
        kind, help_text = HELP.get(name, ('counter', name))
        block(name, kind, help_text, counters[name], suffix_mode=(kind == 'histogram'))
    # Counters that never happened yet are shown as 0, so alerts can use rate().
    for name in ('osqa_emails_sent_total', 'osqa_emails_failed_total'):
        if name not in counters:
            block(name, 'counter', HELP[name][1], [((), 0)])

    try:
        gauges = database_gauges()
        up = 1
    except Exception:
        log.warning('metrics: reading figures from the database failed', exc_info=True)
        gauges, up = {}, 0
    for name, samples in gauges.items():
        block(name, 'gauge', GAUGE_HELP[name], samples)
    block('osqa_database_up', 'gauge', 'Whether the database answered the figures query.', [((), up)])

    stats = _cache_stats()
    for stat, name, kind in (('get_hits', 'osqa_cache_get_hits_total', 'counter'),
                             ('get_misses', 'osqa_cache_get_misses_total', 'counter'),
                             ('evictions', 'osqa_cache_evictions_total', 'counter'),
                             ('curr_items', 'osqa_cache_items', 'gauge'),
                             ('bytes', 'osqa_cache_bytes', 'gauge'),
                             ('limit_maxbytes', 'osqa_cache_limit_bytes', 'gauge')):
        if stat in stats:
            block(name, kind, GAUGE_HELP[name], [((), stats[stat])])
    return '\n'.join(blocks) + '\n'
