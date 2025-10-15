from django.http import HttpResponseRedirect
from forum.forms import get_next_url
import logging
from django.contrib import messages

class CancelActionMiddleware(object):
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

    def process_view(self, request, view_func, view_args, view_kwargs):
        if 'cancel' in request.REQUEST:
            #todo use session messages for the anonymous users
            try:
                msg = getattr(view_func,'CANCEL_MESSAGE')
            except AttributeError:
                msg = 'action canceled'
            messages.info(request, msg)
            return HttpResponseRedirect(get_next_url(request))
        else:
            return None
