from django.http import HttpResponseRedirect, HttpResponse
from forum.forms import get_next_url
from django.utils.translation import gettext as _
from forum import settings
from django.urls import reverse
import logging

from django.contrib import messages

class AnonymousMessageManager(object):
    def __init__(self,request):
        self.request = request
    def create(self,message=''):
        create_message(self.request,message)
    def get_and_delete(self):
        messages = get_and_delete_messages(self.request)
        return messages

def dummy_deepcopy(*arg):
    """this is necessary to prevent deepcopy() on anonymous user object
    that now contains reference to request, which cannot be deepcopied
    """
    return None

class ConnectToSessionMessagesMiddleware(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.process_request(request)
        if response is None:
            response = self.get_response(request)
        return response

    def process_request(self, request):
        from forum.utils.static_files import is_static_request
        if is_static_request(request):
            # Files (media, uploads, the service worker): no session, no greeting,
            # so the response sets no cookie and shared caches may keep it.
            return None
        # Django 1.10+: is_authenticated is a property
        if not request.user.is_authenticated:
            request.user.__deepcopy__ = dummy_deepcopy #plug on deepcopy which may be called by django db "driver"

            #also set the first greeting one time per session only
            if 'greeting_set' not in request.session:
                request.session['greeting_set'] = True

                msg = _('First time here? Check out the <a href="%s">FAQ</a>!') % reverse('faq')

                # Python 3: has_key() -> in operator
                if (settings.STORE_GREETING_IN_COOKIE and 'greeting_set' not in request.COOKIES) or \
                  not settings.STORE_GREETING_IN_COOKIE:
                    messages.info(request, msg)

                if settings.STORE_GREETING_IN_COOKIE:
                    request.COOKIES.set(key='greeting_set', value=True)
