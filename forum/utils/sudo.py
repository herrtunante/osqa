""""Confirm it's you" (sudo mode): sensitive account changes need a recent
sign-in or re-authentication, within the REAUTH_MINUTES setting (default 15).

    from forum.utils.sudo import require_recent_auth, sudo_required

    @sudo_required                       # the whole view
    def view(request): ...

    refused = require_recent_auth(request, next_url=...)   # or only on some paths
    if refused:
        return refused

When the session's last sign-in (accounts.AUTH_TIME_SESSION_KEY, set by
accounts.signed_in() and accounts.mark_reauthenticated()) is older, the member
is sent to /account/confirm/?next=<page> (forum.views.security.confirm_identity):
password, an authenticator or recovery code, a passkey, or (members with
neither a password nor two-step verification) a single-use emailed link. The
form that was sent is not replayed: the member comes back to `next` (a GET page:
the page itself for GET requests, else the form's page) and sends it again.
JSON callers get {"error", "redirect"} instead of a redirect.

Used for: changing the own email address, adding a passkey, setting up and
turning off two-step verification (turning it off and new recovery codes
also take a code or the password in the form itself, which counts as
re-authenticating), creating API tokens, and removing the last sign-in method.
"""
import functools
from urllib.parse import urlencode, urlparse

from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from forum.utils import accounts

LINK_TYPE = 'reauth'
LINK_MINUTES = 15
NEXT_SESSION_KEY = 'osqa_reauth_next'


def safe_next(value):
    """A path on this site, or ''."""
    from forum.forms.general import clean_next
    value = clean_next(value) if value else ''
    return value if value.startswith('/') else ''


def _return_to(request, next_url):
    if next_url:
        return safe_next(next_url)
    if request.method in ('GET', 'HEAD'):
        return safe_next(request.get_full_path())
    referer = request.META.get('HTTP_REFERER', '')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
        parsed = urlparse(referer)
        return safe_next(parsed.path + ('?' + parsed.query if parsed.query else ''))
    return ''


def confirm_url(next_url=''):
    url = reverse('auth_confirm_identity')
    return '%s?%s' % (url, urlencode({'next': next_url})) if next_url else url


def require_recent_auth(request, next_url=None, json=False):
    """None when the member signed in or confirmed it's them recently, else the
    response that sends them to "Confirm it's you" (see the module docstring)."""
    if not request.user.is_authenticated:
        return HttpResponseRedirect(reverse('auth_signin'))
    if accounts.recently_signed_in(request):
        return None
    url = confirm_url(_return_to(request, next_url))
    if json:
        # 200: the script follows `redirect` (a 4xx would also be logged as a failed request).
        return JsonResponse({'error': _("Please confirm it's you first."), 'redirect': url})
    return HttpResponseRedirect(url)


def sudo_required(view=None, next_url=None, json=False):
    """Decorator form of require_recent_auth(). `next_url(request, *args, **kwargs)`
    may name the page to come back to."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(request, *args, **kwargs):
            target = next_url(request, *args, **kwargs) if callable(next_url) else next_url
            refused = require_recent_auth(request, target, json=json)
            return refused if refused is not None else fn(request, *args, **kwargs)
        return wrapper
    return decorate(view) if view is not None else decorate


def sign_in_methods(user):
    """How many ways `user` can sign in: password, sign-in services, passkeys."""
    from forum.models import AuthKeyUserAssociation, Passkey
    return (1 if user.has_usable_password() else 0) + AuthKeyUserAssociation.objects.filter(user=user).count() + \
        Passkey.objects.filter(user=user).count()
