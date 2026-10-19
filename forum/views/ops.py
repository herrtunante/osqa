"""Operations endpoints, answered by ClientIPMiddleware before host checks,
sessions and maintenance mode (like /healthz):

- /readyz: 200 "ok" when the database and the cache answer, else 503 "unavailable"
  (no details). Use it for readiness probes; /healthz (database only) for liveness.
- /metrics: Prometheus metrics (forum/utils/metrics.py). 404 unless METRICS_TOKEN
  is set; then it needs `Authorization: Bearer <METRICS_TOKEN>` (401 without
  the header, 403 with a wrong token).
"""
import hmac
import logging
import secrets

from django.conf import settings as djsettings
from django.core.cache import cache, caches
from django.core.cache.backends.dummy import DummyCache
from django.db import connection
from django.http import HttpResponse

log = logging.getLogger(__name__)

PROMETHEUS_CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8'


def _text(body, status=200, **headers):
    response = HttpResponse(body, status=status, content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    for name, value in headers.items():
        response[name.replace('_', '-')] = value
    return response


def database_ok():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return True
    except Exception:
        log.warning('readyz: database check failed', exc_info=True)
        return False


def cache_ok():
    if isinstance(caches['default'], DummyCache):
        return True
    key, token = 'osqa-readyz', secrets.token_hex(8)
    try:
        cache.set(key, token, 30)
        return cache.get(key) == token
    except Exception:
        log.warning('readyz: cache check failed', exc_info=True)
        return False


def readyz(request):
    if database_ok() and cache_ok():
        return _text('ok\n')
    return _text('unavailable\n', status=503)


def metrics(request):
    token = str(getattr(djsettings, 'METRICS_TOKEN', '') or '')
    if not token:
        return _text('Not Found\n', status=404)
    header = request.META.get('HTTP_AUTHORIZATION', '')
    scheme, _, supplied = header.partition(' ')
    if scheme.lower() != 'bearer' or not supplied.strip():
        return _text('Unauthorized\n', status=401, WWW_Authenticate='Bearer realm="metrics"')
    if not hmac.compare_digest(supplied.strip().encode(), token.encode()):
        from forum.utils import security_log
        security_log.event('metrics_token_refused', request, logging.WARNING)
        return _text('Forbidden\n', status=403)
    from forum.utils.metrics import render
    response = HttpResponse(render(), content_type=PROMETHEUS_CONTENT_TYPE)
    response['Cache-Control'] = 'no-store'
    return response


ENDPOINTS = {'/readyz': readyz, '/metrics': metrics}
