"""The theme switch (header menu for members, footer for everyone): saves the
choice (forum/utils/display.py). osqa.theme.js has already applied it when it
sends this; without JavaScript the form submits and comes back to the page."""
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from forum import settings
from forum.utils import display


@require_POST
def set_theme(request):
    """POST theme=auto|light|dark (and next=<page> for the form). Answers
    {"theme": ...} to a script (Accept: application/json), else redirects."""
    theme = request.POST.get('theme')
    if not settings.DARK_MODE or theme not in display.THEMES:
        return HttpResponseBadRequest('Unknown theme')
    if request.user.is_authenticated:
        display.update_preferences(request.user, theme=theme)
    if 'application/json' in request.META.get('HTTP_ACCEPT', ''):
        response = JsonResponse({'theme': theme})
    else:
        target = request.POST.get('next') or ''
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
            target = '/'
        response = HttpResponseRedirect(target)
    display.set_theme_cookie(request, response, theme)
    return response
