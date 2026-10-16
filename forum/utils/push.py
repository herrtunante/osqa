"""Browser push notifications (Web Push with VAPID keys).

Off, with no trace in the pages, unless VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY
are set (`manage.py generate_vapid_keys` makes a pair). Members turn it on per
device ("Get notifications on this device" on the notifications page and the
email settings page; media/js/osqa.push.js), which stores a PushSubscription.

Every in-site notification (forum/notifications.py notify()) is offered to
send_notifications(), which pushes it to the member's devices when they want
that kind (PUSH_KINDS, chosen on the email settings page). notify() runs in the
action-hook thread, so the request that caused it doesn't wait. Held posts
create no notifications (forum/utils/review.py), so they push nothing either;
inactive (suspended) members are skipped.

What is sent: a short title ("bob answered your question"), the question title
(plain text on one line, control characters removed, cut to BODY_LENGTH characters), and the URL of the notification
(/notifications/<id>/, which marks it read and goes to the post). Never the
text of a post or comment. The payload is encrypted for the browser (RFC 8291)
by pywebpush; the push service only sees its size.
"""
import datetime
import hashlib
import json
import logging
import re
from urllib.parse import urlsplit

from django.conf import settings as djsettings
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from forum.utils import metrics

log = logging.getLogger('forum.push')

TIMEOUT = 5          # seconds per push service request
TTL = 24 * 3600      # the push service keeps an undelivered message for a day
BODY_LENGTH = 120
TITLE_LENGTH = 80
# A subscription that failed this many times in a row (not 404/410) is dropped.
MAX_FAILURES = 10
PROPERTY = 'push_kinds'   # UserProperty key (max 16 characters)

# (key, label, on by default)
PUSH_KINDS = (
    ('answer_mine', _lazy('New answers to my questions'), True),
    ('answer_followed', _lazy('New answers to questions I follow'), False),
    ('comment', _lazy('Comments on my posts'), True),
    ('mention', _lazy('Mentions of my @username'), True),
    ('accepted', _lazy('My answer is accepted'), True),
    ('badge', _lazy('Badges I earn'), False),
    ('review', _lazy('A moderator approves or removes my post'), False),
    ('reminder', _lazy('Reminders to accept an answer'), False),
)
KIND_KEYS = tuple(key for key, label, default in PUSH_KINDS)
DEFAULT_KINDS = tuple(key for key, label, default in PUSH_KINDS if default)

# Push services browsers use; a subscription to anything else is refused, so
# the server never posts to an address a visitor made up (internal hosts...).
PUSH_SERVICE_HOSTS = (
    'fcm.googleapis.com', 'android.googleapis.com',     # Chrome, Edge (Chromium), Opera, Samsung
    '.push.services.mozilla.com',                       # Firefox
    '.notify.windows.com',                              # Edge (legacy)
    '.push.apple.com',                                  # Safari
)


def _setting(name):
    return str(getattr(djsettings, name, '') or '').strip()


def enabled():
    return bool(_setting('VAPID_PUBLIC_KEY') and _setting('VAPID_PRIVATE_KEY'))


def public_key():
    return _setting('VAPID_PUBLIC_KEY')


def subject():
    """Who the push services can contact about our messages: VAPID_SUBJECT
    (mailto: or https: URL), else the site's URL."""
    value = _setting('VAPID_SUBJECT') or str(getattr(djsettings, 'APP_URL', '') or '')
    if value and not value.startswith(('mailto:', 'https:', 'http:')):
        value = 'mailto:' + value
    return value


def endpoint_hash(endpoint):
    return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()


def allowed_endpoint(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host or parts.username or parts.password:
        return False
    hosts = tuple(getattr(djsettings, 'PUSH_SERVICE_HOSTS', ()) or ()) + PUSH_SERVICE_HOSTS
    return any(host == h.lstrip('.') or (h.startswith('.') and host.endswith(h)) for h in hosts)


# -- what a member wants ------------------------------------------------------

def kinds_for(user):
    value = user.prop.push_kinds
    if value is None:
        return set(DEFAULT_KINDS)
    return set(value) & set(KIND_KEYS)


def set_kinds(user, kinds):
    user.prop.push_kinds = sorted(set(kinds) & set(KIND_KEYS))


def kind_of(notification):
    from forum.models import Notification
    kind = notification.kind
    if kind == Notification.ANSWER:
        node = notification.node
        question = getattr(node, 'abs_parent', None) if node is not None else None
        return 'answer_mine' if question is not None and question.author_id == notification.user_id \
            else 'answer_followed'
    if kind in (Notification.APPROVED, Notification.REMOVED):
        return 'review'
    return kind


# -- the message ----------------------------------------------------------------

def plain(text, length):
    """Plain text on one line, at most `length` characters. Kept as typed
    (a title may say "<select> in Collect"): browsers show notification
    texts as plain text, never as HTML, so entities would show literally."""
    text = ''.join(c if c.isprintable() or c.isspace() else ' ' for c in str(text or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= length else text[:length - 1].rstrip() + '…'


def message(notification):
    from forum import settings
    from forum.models import Notification
    from forum.views import pwa
    node = notification.node
    question = None
    if node is not None:
        question = node.abs_parent if node.abs_parent_id else node
    actor = plain(notification.actor.username, 30) if notification.actor_id else _('Someone')
    kind = notification.kind
    if kind == Notification.ANSWER:
        title = (_('%(user)s answered your question') if kind_of(notification) == 'answer_mine'
                 else _('%(user)s answered a question you follow')) % {'user': actor}
    elif kind == Notification.COMMENT:
        title = _('%(user)s commented on your post') % {'user': actor}
    elif kind == Notification.MENTION:
        title = _('%(user)s mentioned you') % {'user': actor}
    elif kind == Notification.ACCEPTED:
        title = _('%(user)s accepted your answer') % {'user': actor}
    elif kind == Notification.BADGE:
        title = _('You earned the %(badge)s badge') % {'badge': plain(notification.text, 40)}
    elif kind == Notification.APPROVED:
        title = _('Your post was approved')
    elif kind == Notification.REMOVED:
        title = _('A moderator removed your post')
    elif kind == Notification.REMINDER:
        title = _('Did an answer solve your question?')
    else:
        title = _('New notification')
    body = question.title if question is not None else settings.APP_SHORT_NAME
    return {
        'title': plain(title, TITLE_LENGTH),
        'body': plain(body, BODY_LENGTH),
        'url': reverse('notification_open', kwargs={'id': notification.id}),
        'id': notification.id,
        # Several notifications about one question show as one.
        'tag': 'osqa-question-%d' % question.id if question is not None else 'osqa-%s' % kind,
        'icon': pwa.icons()[0]['src'],
    }


# -- sending --------------------------------------------------------------------

def webpush(subscription, data):
    """Sends one message (a separate function so tests can replace it)."""
    from pywebpush import webpush as send
    return send(subscription.info(), data=data, vapid_private_key=_setting('VAPID_PRIVATE_KEY'),
                vapid_claims={'sub': subject()}, timeout=TIMEOUT, ttl=TTL)


def send(subscription, payload):
    """Pushes `payload` (a dict) to one device. Returns True when the push
    service took it; deletes the subscription when the service says it no
    longer exists (404, 410), and counts other failures."""
    from forum.models import PushSubscription
    try:
        webpush(subscription, json.dumps(payload))
    except Exception as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        metrics.count('push_failed')
        if status in (404, 410):
            log.info('push: subscription %s is gone (%s), deleted', subscription.id, status)
            PushSubscription.objects.filter(id=subscription.id).delete()
        else:
            log.warning('push: sending to subscription %s failed (%s)', subscription.id, status or type(error).__name__)
            subscription.failure_count += 1
            if subscription.failure_count >= MAX_FAILURES:
                PushSubscription.objects.filter(id=subscription.id).delete()
            else:
                PushSubscription.objects.filter(id=subscription.id).update(failure_count=subscription.failure_count)
        return False
    metrics.count('push_sent')
    subscription.failure_count = 0
    subscription.last_success = datetime.datetime.now()
    PushSubscription.objects.filter(id=subscription.id).update(failure_count=0, last_success=subscription.last_success)
    return True


def send_notifications(notifications):
    """Pushes new in-site notifications to the devices of their owners who
    want that kind. Never raises (a push problem must not break the action)."""
    if not notifications or not enabled():
        return 0
    try:
        return _send_notifications(notifications)
    except Exception:
        log.exception('push: could not send notifications')
        return 0


def _send_notifications(notifications):
    from forum.models import Notification, PushSubscription
    from forum.utils.review import can_view
    subscriptions = {}
    for subscription in PushSubscription.objects.filter(
            user_id__in={n.user_id for n in notifications}, user__is_active=True).select_related('user'):
        subscriptions.setdefault(subscription.user_id, []).append(subscription)
    sent = 0
    for notification in notifications:
        devices = subscriptions.get(notification.user_id)
        if not devices:
            continue
        user = devices[0].user
        if user.is_suspended() or kind_of(notification) not in kinds_for(user):
            continue
        if notification.node is not None and not can_view(notification.node, user):
            continue
        if notification.pk is None:
            # bulk_create doesn't return ids on every database (MySQL).
            notification = Notification.objects.filter(
                user_id=notification.user_id, kind=notification.kind, node=notification.node).order_by('-id').first()
            if notification is None:
                continue
        payload = message(notification)
        for subscription in devices:
            sent += send(subscription, payload)
    return sent


# -- devices --------------------------------------------------------------------

def device_label(user_agent):
    """'Chrome on Android' from a User-Agent header (for the device list)."""
    ua = user_agent or ''
    browser = ('Edge' if 'Edg/' in ua else 'Opera' if 'OPR/' in ua else 'Samsung Internet' if 'SamsungBrowser' in ua
               else 'Firefox' if ('Firefox/' in ua or 'FxiOS' in ua) else 'Chrome' if ('Chrome/' in ua or 'CriOS' in ua)
               else 'Safari' if 'Safari/' in ua else '')
    system = ('Android' if 'Android' in ua else 'iOS' if re.search(r'iPhone|iPad|iPod', ua) else 'Windows'
              if 'Windows' in ua else 'macOS' if 'Mac OS X' in ua else 'ChromeOS' if 'CrOS' in ua
              else 'Linux' if 'Linux' in ua else '')
    if browser and system:
        return _('%(browser)s on %(system)s') % {'browser': browser, 'system': system}
    return browser or system or _('Unknown browser')


def generate_keys():
    """A new VAPID key pair as (public, private): the public key is the
    uncompressed P-256 point (what PushManager.subscribe takes as
    applicationServerKey), the private key the raw 32-byte secret, both
    base64url without padding."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, 'big')
    return b64(public), b64(private)
