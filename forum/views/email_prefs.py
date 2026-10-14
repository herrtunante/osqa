"""Unsubscribing from notification emails without logging in, and the help
page about email notifications."""
from django.contrib import messages
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _

from forum.models import MarkedTag, Tag
from django.views.decorators.csrf import csrf_exempt

from forum.utils import unsubscribe


@csrf_exempt  # the signed token is the credential; mail programs POST without a CSRF token
def email_unsubscribe(request, token):
    found = unsubscribe.read_token(token)
    if found is None:
        return render(request, 'email/unsubscribe.html', {'invalid': True}, status=404)
    user, scope, question = found

    # RFC 8058 one-click: the mail program POSTs "List-Unsubscribe=One-Click".
    if request.method == 'POST' and request.POST.get('List-Unsubscribe') == 'One-Click':
        unsubscribe.apply(user, scope, question)
        return HttpResponse('Unsubscribed.', content_type='text/plain')

    state = 'confirm'
    if request.method == 'POST':
        if request.POST.get('action') == 'undo':
            unsubscribe.undo(user, scope, question)
            state = 'restored'
        else:
            unsubscribe.apply(user, scope, question)
            state = 'done'
    # GET only shows the page: link checkers and mail scanners that open the
    # link must not unsubscribe anyone.
    return render(request, 'email/unsubscribe.html', {
        'state': state, 'scope': scope, 'what': unsubscribe.SCOPES[scope][0], 'question': question,
        'person': user, 'token': token,
        'all_token': unsubscribe.make_token(user, 'all') if scope != 'all' else None,
        'manage_url': user.get_user_subscriptions_url(),
        'own_page': request.user.is_authenticated and request.user == user,
    })


def email_help(request):
    return render(request, 'email/help.html', {})


def follow_tag(request, tag):
    """Follow (or stop following) a tag: following means an email for each new question in it."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tag = get_object_or_404(Tag, name=tag)
    if not request.user.is_authenticated:
        return HttpResponseRedirect('%s?next=%s' % (reverse('auth_signin'), tag.get_absolute_url()))
    user = request.user
    if request.POST.get('action') == 'unfollow':
        MarkedTag.objects.filter(user=user, tag=tag, reason='good').delete()
        messages.info(request, _("You no longer follow %s.") % tag.name)
    else:
        MarkedTag.objects.update_or_create(user=user, tag=tag, defaults={'reason': 'good'})
        prefs = user.subscription_settings
        if prefs.new_question_watched_tags == 'n':
            prefs.new_question_watched_tags = 'i'  # following a tag means wanting to hear about it
            prefs.save()
        if prefs.enable_notifications:
            messages.info(request, _("You follow %s: you'll get an email for each new question in it.") % tag.name)
        else:
            messages.info(request, _("You follow %s, but your email notifications are switched off. "
                                     "Turn them on in your email settings to get emails.") % tag.name)
    return HttpResponseRedirect(tag.get_absolute_url())
