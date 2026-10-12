import threading


class RequestHolder(object):
    _requests = threading.local()

    @property
    def request(self):
        # None outside a request, e.g. in the background thread running action hooks.
        return getattr(self._requests, 'request', None)

    @request.setter
    def request(self, value):
        self._requests.request = value

REQUEST_HOLDER = RequestHolder()
