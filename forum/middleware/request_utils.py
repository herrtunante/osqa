import forum
from django.http import QueryDict

from forum.settings import MAINTAINANCE_MODE, APP_LOGO, APP_TITLE

from forum.http_responses import HttpResponseServiceUnavailable

ALWAYS_SERVED = ('/sw.js', '/manifest.webmanifest')



class RequestUtils(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = None
        if hasattr(self, "process_request"):
            response = self.process_request(request)
        if response is None:
            response = self.get_response(request)
        if hasattr(self, "process_response"):
            response = self.process_response(request, response)
        return response

    def process_request(self, request):
        # The service worker and the manifest are static and must stay reachable,
        # or the browser reports a failed service worker update on the maintenance page.
        if MAINTAINANCE_MODE.value is not None and isinstance(MAINTAINANCE_MODE.value.get('allow_ips', None), list) \
                and request.path_info not in ALWAYS_SERVED:
            ip = request.META['REMOTE_ADDR']

            if not ip in MAINTAINANCE_MODE.value['allow_ips']:
                return HttpResponseServiceUnavailable(MAINTAINANCE_MODE.value.get('message', ''))

        if request.session.get('redirect_POST_data', None):
            # Stored urlencoded: sessions are JSON-serialized.
            request.POST = QueryDict(request.session.pop('redirect_POST_data'))
            request.META['REQUEST_METHOD'] = "POST"

        self.request = request
        forum.REQUEST_HOLDER.request = request
        return None

    def process_response(self, request, response):
        forum.REQUEST_HOLDER.request = None
        return response

