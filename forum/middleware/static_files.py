"""Answer requests for files (skin media, Django's admin media, uploads)
without the session, user, language and message middleware.

Placed after the security middleware (so the security headers are still
added), it calls the file views of forum.views.meta directly: no session row
is created or read, no cookie is set, and no `Vary: Cookie, Accept-Language`
keeps shared caches from storing the response. Anything unusual (a missing
file, a bad path) goes through the normal stack, so error pages are rendered
as usual.
"""
import re

from django.core.exceptions import SuspiciousOperation
from django.http import Http404
from django.urls import Resolver404, resolve

FILE_PATHS = re.compile(r'^/(m|upfiles)/')


class StaticFilesMiddleware(object):
    def __init__(self, get_response):
        self.get_response = get_response
        from forum.views import meta
        self.views = (meta.media, meta.django_admin_media, meta.uploaded_file)

    def __call__(self, request):
        if request.method in ('GET', 'HEAD') and FILE_PATHS.match(request.path_info):
            try:
                match = resolve(request.path_info)
            except Resolver404:
                match = None
            if match is not None and match.func in self.views:
                try:
                    return match.func(request, *match.args, **match.kwargs)
                except (Http404, SuspiciousOperation):
                    pass
        return self.get_response(request)
