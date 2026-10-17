"""
A two-part middleware that lets request handling code set or delete cookies
through request.COOKIES:

    request.COOKIES.set('greeting_set', True, max_age=3600)
    request.COOKIES.delete('greeting_set')

request.COOKIES stays a plain dict of strings (Django and its middleware,
e.g. LocaleMiddleware, read it); the changes are applied to the response by
CookiePostHandlerMiddleware.

MIDDLEWARE = [
    'forum.middleware.django_cookies.CookiePreHandlerMiddleware',
    ...
    'forum.middleware.django_cookies.CookiePostHandlerMiddleware',
]
"""


class CookieHandler(dict):
    def __init__(self, cookies):
        super().__init__(cookies)
        self.changes = {}

    def set(self, key, value='', max_age=None, expires=None, path='/', domain=None, secure=None):
        """Matches django.http.HttpResponse.set_cookie."""
        self[key] = str(value)
        self.changes[key] = dict(value=str(value), max_age=max_age, expires=expires, path=path, domain=domain,
                                 secure=bool(secure), samesite='Lax')

    def delete(self, key, path='/', domain=None):
        """Matches django.http.HttpResponse.delete_cookie."""
        self.pop(key, None)
        self.changes[key] = dict(delete=True, path=path, domain=domain)


class CookiePreHandlerMiddleware(object):
    """Wraps request.COOKIES. This should be the first middleware you load."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.COOKIES = CookieHandler(request.COOKIES)
        return self.get_response(request)


class CookiePostHandlerMiddleware(object):
    """Applies the cookies set or deleted through request.COOKIES. This should
    be the last middleware you load."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        for key, change in getattr(request.COOKIES, 'changes', {}).items():
            if change.pop('delete', False):
                response.delete_cookie(key, **change)
            else:
                response.set_cookie(key, **change)
        return response
