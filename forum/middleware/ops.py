"""First middleware: the request id and request metrics.

Sets request.request_id (the proxy's X-Request-ID or a new one, see
forum/utils/logs.py), makes it available to logging and Sentry for the
duration of the request, returns it in the X-Request-ID response header, and
counts the request by view name and status class with its duration
(forum/utils/metrics.py; buffered in memory, no I/O per request). Probe and
metrics URLs are not counted.
"""
import time

from forum.utils import logs, metrics

NOT_COUNTED = ('/healthz', '/readyz', '/metrics')


def view_label(request):
    match = getattr(request, 'resolver_match', None)
    if match is not None and match.view_name:
        return match.view_name
    path = request.path_info
    if path.startswith('/m/') or path.startswith('/upfiles/'):
        return 'static'
    return 'unmatched'


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        request.request_id = logs.request_id_from(request)
        tokens = logs.bind_request(request, request.request_id)
        try:
            response = self.get_response(request)
            response[logs.REQUEST_ID_HEADER] = request.request_id
            if request.path_info not in NOT_COUNTED:
                metrics.observe_request(view_label(request), request.method, response.status_code,
                                        time.perf_counter() - started)
            return response
        finally:
            logs.unbind_request(tokens)
