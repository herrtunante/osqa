"""Browser push: the subscribe/unsubscribe calls of media/js/osqa.push.js and
the form of the email settings page (which kinds, remove a device).
All 404 unless VAPID keys are configured (forum/utils/push.py)."""
import base64
import binascii
import json
import re

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from forum.models import PushSubscription
from forum.utils import push

MAX_DEVICES = 20
MAX_ENDPOINT = 2048


def _b64decode(value):
    """Base64url (as browsers give subscription keys); ValueError otherwise."""
    if not isinstance(value, str) or len(value) > 200 or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value):
        raise ValueError
    value = value.rstrip('=')
    try:
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise ValueError


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _json_body(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _guard(request):
    if not push.enabled():
        raise Http404
    if not request.user.is_authenticated:
        return _error(_('Please sign in first.'), 403)
    return None


@require_POST
def subscribe(request):
    """{"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}: what
    PushSubscription.toJSON() gives in the browser."""
    refused = _guard(request)
    if refused:
        return refused
    data = _json_body(request)
    if data is None:
        return _error(_('Invalid request.'))
    endpoint = data.get('endpoint')
    keys = data.get('keys') if isinstance(data.get('keys'), dict) else {}
    if not isinstance(endpoint, str) or len(endpoint) > MAX_ENDPOINT or not push.allowed_endpoint(endpoint):
        return _error(_('This browser\'s push service is not supported.'))
    try:
        p256dh, auth = _b64decode(keys.get('p256dh')), _b64decode(keys.get('auth'))
    except ValueError:
        return _error(_('Invalid request.'))
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
        return _error(_('Invalid request.'))

    label = push.device_label(request.META.get('HTTP_USER_AGENT', ''))[:64]
    digest = push.endpoint_hash(endpoint)
    subscription = PushSubscription.objects.filter(endpoint_hash=digest).first()
    if subscription is None:
        subscription = PushSubscription(endpoint_hash=digest, endpoint=endpoint)
    # The same browser signed in as someone else now: the device is theirs.
    subscription.user = request.user
    subscription.p256dh = keys['p256dh']
    subscription.auth = keys['auth']
    subscription.label = label
    subscription.failure_count = 0
    subscription.save()
    old = PushSubscription.objects.filter(user=request.user).values_list('id', flat=True)[MAX_DEVICES:]
    PushSubscription.objects.filter(id__in=list(old)).delete()
    return JsonResponse({'ok': True, 'device': label})


@require_POST
def unsubscribe(request):
    """{"endpoint": ...}: this browser turned push off."""
    refused = _guard(request)
    if refused:
        return refused
    data = _json_body(request)
    if data is None or not isinstance(data.get('endpoint'), str):
        return _error(_('Invalid request.'))
    removed, _details = PushSubscription.objects.filter(
        user=request.user, endpoint_hash=push.endpoint_hash(data['endpoint'])).delete()
    return JsonResponse({'ok': True, 'removed': removed})


@require_POST
def push_settings(request):
    """The form on the email settings page: which notifications to push, or
    `remove=<id>` to forget a device."""
    if not push.enabled():
        raise Http404
    if not request.user.is_authenticated:
        return HttpResponseRedirect('/')
    if request.POST.get('remove'):
        try:
            removed, _details = PushSubscription.objects.filter(user=request.user,
                                                                id=int(request.POST['remove'])).delete()
        except ValueError:
            removed = 0
        if removed:
            messages.info(request, _('The device was removed. It gets no more notifications from this site.'))
    else:
        push.set_kinds(request.user, request.POST.getlist('kinds'))
        messages.info(request, _('Your device notification settings are saved.'))
    target = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = request.user.get_user_subscriptions_url()
    return HttpResponseRedirect(target + '#push-settings' if '#' not in target else target)
