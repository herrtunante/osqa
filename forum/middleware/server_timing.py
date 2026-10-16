"""Server-Timing header, for profiling pages in the browser's developer tools
(Network > Timing) without a toolbar:

    Server-Timing: db;dur=12.4;desc="9 queries", render;dur=31.0, total;dur=58.2

- db: time spent in database queries, with their number;
- render: time spent rendering templates (the outermost template of each
  render; includes run inside it);
- total: the rest of the middleware chain and the view, from here on.

Added for every request when DEBUG is on, otherwise only for staff members
when the SERVER_TIMING setting (settings_local.py or the environment) is set.
Without either it does nothing. The figures are about this site's own code
(no personal data), but they help to time requests, so ordinary visitors
never get them in production.
"""
import contextvars
import time
from contextlib import ExitStack

from django.conf import settings as django_settings
from django.db import connections
from django.template import base as template_base

_timer = contextvars.ContextVar('osqa_server_timing', default=None)


class _Timer:
    __slots__ = ('db', 'queries', 'render', 'depth')

    def __init__(self):
        self.db = 0.0
        self.queries = 0
        self.render = 0.0
        self.depth = 0

    def query(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.db += time.perf_counter() - start
            self.queries += 1


def _time_templates():
    """Times Template.render while a request is being timed (once per process)."""
    original = template_base.Template.render
    if getattr(original, 'osqa_timed', False):
        return

    def render(self, context):
        timer = _timer.get()
        if timer is None:
            return original(self, context)
        timer.depth += 1
        start = time.perf_counter() if timer.depth == 1 else None
        try:
            return original(self, context)
        finally:
            timer.depth -= 1
            if start is not None:
                timer.render += time.perf_counter() - start

    render.osqa_timed = True
    template_base.Template.render = render


def enabled(request):
    if django_settings.DEBUG:
        return True
    if not getattr(django_settings, 'SERVER_TIMING', ''):
        return False
    user = getattr(request, 'user', None)
    return bool(user is not None and user.is_authenticated and user.is_staff)


def header(timer, total):
    return 'db;dur=%.1f;desc="%d queries", render;dur=%.1f, total;dur=%.1f' % (
        timer.db * 1000, timer.queries, timer.render * 1000, total * 1000)


class ServerTimingMiddleware:
    """Place after the middleware that sets request.user (ExtendedUser)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not enabled(request):
            return self.get_response(request)
        _time_templates()
        timer = _Timer()
        token = _timer.set(timer)
        start = time.perf_counter()
        try:
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(timer.query))
                response = self.get_response(request)
        finally:
            _timer.reset(token)
        response['Server-Timing'] = header(timer, time.perf_counter() - start)
        return response
