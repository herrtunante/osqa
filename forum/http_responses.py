from django.http import HttpResponse
from django.template.loader import render_to_string

from forum import settings

class HttpResponseServiceUnavailable(HttpResponse):
    def __init__(self, message):
        super(HttpResponseServiceUnavailable, self).__init__(
            content=render_to_string('503.html', {
                'message': message,
                'app_logo': settings.APP_LOGO,
                'app_title': settings.APP_TITLE
            }), status=503)

class HttpResponseUnauthorized(HttpResponse):
    def __init__(self, request):
        if request.user.is_authenticated:
            super(HttpResponseUnauthorized, self).__init__(
                content=render_to_string('403.html', request=request),
                status=403
            )
        else:
            super(HttpResponseUnauthorized, self).__init__(
                content=render_to_string('401.html', request=request),
                status=401
            )

class HttpResponseNotFound(HttpResponse):
    def __init__(self, request):
        super(HttpResponseNotFound, self).__init__(
            content=render_to_string('404.html', request=request),
            status=404
        )

class HttpResponseIntServerError(HttpResponse):
    def __init__(self, request):
        try:
            content = render_to_string('500.html', request=request)
        except Exception:
            # Whatever broke the request may break the context processors too.
            content = render_to_string('500.html')
        super(HttpResponseIntServerError, self).__init__(content=content, status=500)