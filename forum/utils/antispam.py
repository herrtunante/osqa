"""Defences against bot sign-ups and profile spam (no external services)."""
import time

from django import forms
from django.core import signing
from django.core.cache import cache
from django.utils.html import format_html
from django.utils.translation import gettext as _

from forum import settings

SIGNUP_SALT = 'forum.signup-form'


class BotTrapMixin(forms.Form):
    """Adds two hidden fields to a sign-up form: a honeypot that people never
    see (bots fill in every field) and a signed time stamp of when the form
    was shown (bots send it back within a second or two)."""

    homepage = forms.CharField(required=False)
    form_token = forms.CharField(required=False)

    def bot_traps(self):
        # A fresh time stamp on every render, so a form shown again after an
        # error never carries an expired one.
        return format_html(
            '<div class="form-trap" aria-hidden="true" '
            'style="position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden">'
            '<label for="id_homepage">{}</label>'
            '<input type="text" name="homepage" id="id_homepage" value="" tabindex="-1" autocomplete="off">'
            '</div><input type="hidden" name="form_token" value="{}">',
            _("Leave this field empty"), signing.dumps(time.time(), salt=SIGNUP_SALT))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('homepage'):
            raise forms.ValidationError(_("Sorry, your registration could not be completed. Please try again."))
        try:
            elapsed = time.time() - float(signing.loads(cleaned.get('form_token') or '', salt=SIGNUP_SALT))
        except (signing.BadSignature, TypeError, ValueError):
            elapsed = None
        if elapsed is None or elapsed > 24 * 3600:
            raise forms.ValidationError(_("This form has expired. Please fill it in again."))
        if elapsed < int(settings.SIGNUP_MIN_SECONDS):
            raise forms.ValidationError(_("That was quick! Please check the form and send it again."))
        return cleaned


def _signup_key(request):
    return 'signups-from:%s' % request.META.get('REMOTE_ADDR', '')


def signup_limit_reached(request):
    limit = int(settings.SIGNUP_LIMIT_PER_IP)
    return limit > 0 and cache.get(_signup_key(request), 0) >= limit


def count_signup(request):
    key = _signup_key(request)
    if not cache.add(key, 1, 3600):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, 3600)


def links_trusted(user):
    """Whether links in this user's profile may be shown as links."""
    return user.is_staff or user.is_superuser or int(user.reputation) >= int(settings.PROFILE_LINKS_MIN_REP)


# Actions that don't show a person using the forum: what a bot account does.
DORMANT_ACTIONS = ('userjoins', 'userlogin', 'editprofile', 'award')


def dormant_users(days):
    """Accounts older than `days` that never confirmed their email, never posted,
    voted, flagged or did anything else, and haven't logged in or used the site
    while logged in within that time. Staff accounts are never included."""
    from datetime import datetime, timedelta
    from django.db.models import Exists, OuterRef, Q
    from forum.models import Action, Node, User

    cutoff = datetime.now() - timedelta(days=days)
    return User.objects.filter(
        date_joined__lt=cutoff, email_isvalid=False, is_staff=False, is_superuser=False,
    ).filter(
        Q(last_login__isnull=True) | Q(last_login__lt=cutoff), last_seen__lt=cutoff,
    ).exclude(
        Exists(Node.objects.filter(author=OuterRef('pk'))),
    ).exclude(
        Exists(Action.objects.filter(user=OuterRef('pk')).exclude(action_type__in=DORMANT_ACTIONS)),
    ).order_by('date_joined')
