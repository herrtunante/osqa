import sentry_sdk


class SentryUserMiddleware:
    """Tags server errors with the signed-in user's id (no name, email or IP),
    so reports can be matched to a person who writes in about a problem."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            sentry_sdk.set_user({'id': str(user.id)})
        return self.get_response(request)
