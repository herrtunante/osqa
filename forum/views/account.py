"""Users download their data or delete their account."""
import datetime
import json

from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _

from forum import settings
from forum.models import (User, Question, Answer, Comment, Action, MarkedTag, QuestionSubscription,
                          AuthKeyUserAssociation, ValidationHash, Award)


def _own_account(request, id):
    if not request.user.is_authenticated or request.user.id != int(id):
        raise Http404
    return get_object_or_404(User, id=id)


def _date(value):
    return value.isoformat() if value else None


def _post(node):
    revision = node.active_revision
    data = {
        'id': node.id,
        'url': settings.APP_BASE_URL + node.get_absolute_url(),
        'created': _date(node.added_at),
        'score': node.score,
        'deleted': bool(node.nis.deleted),
        'text': revision.body if revision else node.body,
    }
    if node.node_type == 'question':
        data['title'] = node.title
        data['tags'] = node.tagname_list()
    return data


def account_data(request, id):
    """The "your data and account" page."""
    user = _own_account(request, id)
    return render(request, 'users/data.html', {
        'view_user': user,
        **_deletion_context(request, user),
    })


def _deletion_context(request, user):
    from forum.utils import accounts, two_factor
    has_password = user.has_usable_password()
    totp = two_factor.is_enabled(user)
    return {
        'needs_password': has_password,
        'needs_code': totp,
        # Accounts with neither (a sign-in service only) sign in again first.
        'needs_recent_signin': not has_password and not totp and not accounts.recently_signed_in(request),
        'recent_minutes': accounts.reauth_minutes(),
        'last_admin': _is_last_admin(user),
        'is_staff_account': user.is_staff or user.is_superuser,
        'posts_deleted': str(settings.ACCOUNT_DELETION_POSTS) == 'delete',
    }


def export_data(request, id):
    """Everything the site stores about the user, as a JSON download."""
    user = _own_account(request, id)

    votes = [{'post': settings.APP_BASE_URL + a.node.leaf.get_absolute_url() if a.node else None,
              'vote': a.action_type, 'date': _date(a.action_date)}
             for a in Action.objects.filter(user=user, canceled=False, action_type__in=('voteup', 'votedown'))
                                    .select_related('node')]
    favorites = [settings.APP_BASE_URL + a.node.leaf.get_absolute_url()
                 for a in Action.objects.filter(user=user, canceled=False, action_type='favorite').select_related('node')
                 if a.node]

    try:
        subscription = user.subscription_settings
        notifications = {f.name: getattr(subscription, f.name) for f in subscription._meta.fields
                         if f.name not in ('id', 'user')}
    except Exception:
        notifications = {}

    data = {
        'exported_at': _date(datetime.datetime.now()),
        'site': settings.APP_URL,
        'profile': {
            'username': user.username,
            'real_name': user.real_name,
            'email': user.email,
            'email_validated': user.email_isvalid,
            'website': user.website,
            'location': user.location,
            'date_of_birth': _date(user.date_of_birth),
            'about': user.about,
            'date_joined': _date(user.date_joined),
            'last_seen': _date(user.last_seen),
            'reputation': user.reputation,
        },
        'login_methods': sorted(set(AuthKeyUserAssociation.objects.filter(user=user).values_list('provider', flat=True))),
        'badges': [{'badge': str(a.badge.name), 'date': _date(a.awarded_at)}
                   for a in Award.objects.filter(user=user).select_related('badge')],
        'questions': [_post(q) for q in Question.objects.filter(author=user).order_by('added_at')],
        'answers': [_post(a) for a in Answer.objects.filter(author=user).order_by('added_at')],
        'comments': [_post(c) for c in Comment.objects.filter(author=user).order_by('added_at')],
        'votes': votes,
        'favorites': favorites,
        'tags': {
            'interesting': list(MarkedTag.objects.filter(user=user, reason='good').values_list('tag__name', flat=True)),
            'ignored': list(MarkedTag.objects.filter(user=user, reason='bad').values_list('tag__name', flat=True)),
        },
        'notification_settings': notifications,
    }

    response = HttpResponse(json.dumps(data, indent=2, ensure_ascii=False), content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="%s-%s-data-%s.json"' % (
        slugify(str(settings.APP_SHORT_NAME)) or 'osqa', slugify(user.username), datetime.date.today().isoformat())
    return response


def _is_last_admin(user):
    return user.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(id=user.id).exists()


def _reauthenticate(request, user):
    """Checks the password and/or two-step code typed to confirm the deletion
    (or a recent sign-in, for accounts that have neither). Returns an error."""
    from forum.utils import accounts, two_factor
    from forum.views.auth import count_login_failure, login_paused
    if user.has_usable_password():
        if login_paused(request, user.username):
            return _("Too many wrong passwords. Wait %(minutes)s minutes and try again.") % {
                'minutes': int(settings.LOGIN_LOCKOUT_MINUTES)}
        if not user.check_password(request.POST.get('password', '')):
            count_login_failure(request, user.username)
            return _("The password is not correct.")
    if two_factor.is_enabled(user):
        result = two_factor.check_code(request, user, request.POST.get('code', ''), 'delete_account')
        if result == two_factor.PAUSED:
            return _("Too many wrong codes. Wait %(minutes)s minutes and try again.") % {
                'minutes': two_factor.lockout_minutes()}
        if result not in (two_factor.OK_TOTP, two_factor.OK_RECOVERY):
            return _("The code from your authenticator app is not right.")
    elif not user.has_usable_password() and not accounts.recently_signed_in(request):
        return _("For your security, sign in again, then delete your account within %(minutes)s minutes.") % {
            'minutes': accounts.reauth_minutes()}
    return None


def delete_account(request, id):
    """Deletes the account after the member confirms who they are: personal
    data is removed, every session ends, a last email confirms it, and the
    posts stay without the name (or are deleted: ACCOUNT_DELETION_POSTS).
    Moderators and administrators can't delete their own account."""
    from forum.utils import security_log
    user = _own_account(request, id)
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('user_account_data', kwargs={'id': user.id}))

    error = None
    if _is_last_admin(user):
        error = _("You are the only administrator. Make someone else an administrator first.")
    elif user.is_staff or user.is_superuser:
        error = _("Moderators and administrators can't delete their account. Ask an administrator to remove "
                  "your moderator or administrator status first.")
    elif request.POST.get('confirm_username', '').strip() != user.username:
        error = _("Type your username exactly to confirm.")
    else:
        error = _reauthenticate(request, user)

    if error:
        security_log.event('account_deletion_refused', request, account=user)
        messages.error(request, error)
        return HttpResponseRedirect(reverse('user_account_data', kwargs={'id': user.id}))

    posts_deleted = delete_user_account(user, request)
    logout(request)
    if posts_deleted:
        messages.info(request, _("Your account and your posts have been deleted. We sent a last email to confirm it."))
    else:
        messages.info(request, _("Your account has been deleted. Your posts remain, without your name. We sent a "
                                 "last email to confirm it."))
    return HttpResponseRedirect(reverse('index'))


def delete_user_account(user, request=None):
    """Removes the personal data of `user` and signs them out everywhere;
    emails them a confirmation first. Returns whether their posts were deleted.
    Logged without personal data (the account id only)."""
    from forum.utils import accounts, security_log
    posts_deleted = str(settings.ACCOUNT_DELETION_POSTS) == 'delete'
    if user.email:
        accounts.send([accounts.recipient(user)], 'auth/account_deleted_email.html', {
            'deleted_name': user.username, 'posts_deleted': posts_deleted})
    if posts_deleted:
        _delete_posts(user, request)
    anonymise_user(user)
    security_log.event('account_deleted', None, account_id=user.id, posts=('deleted' if posts_deleted else 'kept'))
    return posts_deleted


def _delete_posts(user, request):
    from forum.actions import DeleteAction
    from forum.models import Node
    ip = request.META.get('REMOTE_ADDR', '') if request is not None else ''
    # Comments and answers first, so a question is deleted after what hangs on it.
    order = {'comment': 0, 'answer': 1, 'question': 2}
    nodes = sorted(Node.objects.filter(author=user), key=lambda n: order.get(n.node_type, 3))
    for node in nodes:
        node = node.leaf
        if not node.nis.deleted:
            DeleteAction(user=user, node=node, ip=ip).save()


def _forget_action_ips(user):
    """The actions stay (votes, revisions, reputation); the IP addresses they
    recorded (every sign-in, post and vote) are removed, from the object cache too."""
    from django.core.cache import cache
    from django.db.models import Q
    from forum.models.action import ActionProxyMetaClass
    rows = list(Action.objects.filter(Q(user=user) | Q(canceled_by=user)).values_list('id', 'action_type'))
    Action.objects.filter(user=user).exclude(ip='').update(ip='')
    Action.objects.filter(canceled_by=user).exclude(canceled_ip='').update(canceled_ip='')
    keys = []
    for action_id, action_type in rows:
        keys.append(Action._generate_cache_key(action_id))
        proxy = ActionProxyMetaClass.types.get(action_type)
        if proxy is not None:
            keys.append(proxy._generate_cache_key(action_id))
    if keys:
        cache.delete_many(keys)


def anonymise_user(user):
    """Personal data out, the account unusable; content and votes stay."""
    from forum.models import (ApiToken, Notification, PendingEmail, Passkey, PushSubscription, RecoveryCode,
                              TOTPDevice)
    from forum.models.user import UserProperty
    user.username = 'deleted-user-%d' % user.id
    user.first_name = user.last_name = ''
    user.real_name = ''
    user.email = ''
    user.email_isvalid = False
    user.email_confirmation_required = False
    user.website = ''
    user.location = ''
    user.about = ''
    user.date_of_birth = None
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.set_unusable_password()  # also ends every session (their auth hash changes)
    user.session_epoch = (user.session_epoch or 0) + 1
    user.save()

    _forget_action_ips(user)
    AuthKeyUserAssociation.objects.filter(user=user).delete()
    ValidationHash.objects.filter(user=user).delete()
    MarkedTag.objects.filter(user=user).delete()
    QuestionSubscription.objects.filter(user=user).delete()
    Passkey.objects.filter(user=user).delete()
    TOTPDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
    PushSubscription.objects.filter(user=user).delete()
    ApiToken.objects.filter(user=user).delete()
    Notification.objects.filter(user=user).delete()
    PendingEmail.objects.filter(user=user).delete()
    UserProperty.objects.filter(user=user).delete()  # preferences, pending email change, known browsers
    user.__dict__.pop('_prop', None)
    try:
        user.subscription_settings.enable_notifications = False
        user.subscription_settings.send_digest = False
        user.subscription_settings.save()
    except Exception:
        pass
