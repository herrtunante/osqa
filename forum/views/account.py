"""Users download their data or delete their account."""
import datetime
import json
import logging

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
        'needs_password': user.has_usable_password(),
        'last_admin': _is_last_admin(user),
    })


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
        'badges': [{'badge': a.badge.name, 'date': _date(a.awarded_at)}
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


def delete_account(request, id):
    """Anonymise the account: posts stay (they are part of other people's threads) but no longer
    carry the person's name or any personal data, and the account can no longer be used."""
    user = _own_account(request, id)
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('user_account_data', kwargs={'id': user.id}))

    error = None
    if _is_last_admin(user):
        error = _("You are the only administrator. Make someone else an administrator first.")
    elif request.POST.get('confirm_username', '').strip() != user.username:
        error = _("Type your username exactly to confirm.")
    elif user.has_usable_password() and not user.check_password(request.POST.get('password', '')):
        error = _("The password is not correct.")

    if error:
        messages.error(request, error)
        return HttpResponseRedirect(reverse('user_account_data', kwargs={'id': user.id}))

    old_name = user.username
    anonymise_user(user)
    logout(request)
    logging.info("User %s (id %s) deleted their account", old_name, user.id)
    messages.info(request, _("Your account has been deleted. Your posts remain, without your name."))
    return HttpResponseRedirect(reverse('index'))


def anonymise_user(user):
    user.username = 'deleted-user-%d' % user.id
    user.first_name = user.last_name = ''
    user.real_name = ''
    user.email = ''
    user.email_isvalid = False
    user.website = ''
    user.location = ''
    user.about = ''
    user.date_of_birth = None
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.set_unusable_password()
    user.save()

    AuthKeyUserAssociation.objects.filter(user=user).delete()
    ValidationHash.objects.filter(user=user).delete()
    MarkedTag.objects.filter(user=user).delete()
    QuestionSubscription.objects.filter(user=user).delete()
    try:
        user.subscription_settings.enable_notifications = False
        user.subscription_settings.send_digest = False
        user.subscription_settings.save()
    except Exception:
        pass
