import sentry_sdk

from forum.utils.sentry import set_visitor, reset_visitor


class SentryUserMiddleware:
    """Tags server errors with the signed-in user's id (no name, email or IP),
    so reports can be matched to a person who writes in about a problem, and
    tells the per-visitor event limit who the request is for."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            sentry_sdk.set_user({'id': str(user.id)})
        token = set_visitor(request)
        try:
            return self.get_response(request)
        finally:
            reset_visitor(token)
