from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth import logout
from forum.models.user import AnonymousUser
from forum.views.auth import forward_suspended_user
import logging

class ExtendedUser(AuthenticationMiddleware):
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)

    def __call__(self, request):
        # Process request
        response = self.process_request(request)
        if response is None:
            response = self.get_response(request)
        return response

    def process_request(self, request):
        super(ExtendedUser, self).process_request(request)
        # Django 1.10+: is_authenticated is a property, not a method
        if request.user.is_authenticated:
            try:
                request.user = request.user.user

                if request.user.is_suspended():
                    user = request.user
                    logout(request)
                    return forward_suspended_user(request, user)

                return None
            except Exception as e:
                import traceback
                logging.error("Unable to convert auth_user %s to forum_user: \n%s" % (
                    request.user.id, traceback.format_exc()
                ))

        request.user = AnonymousUser()
        return None