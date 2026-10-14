"""Unsubscribe links for notification emails.

Every notification email carries signed links that work without logging in:
one to stop that kind of email (this question, new questions, mentions,
new members, the digest) and one to stop all emails. The same "all" link is
sent as the List-Unsubscribe header (RFC 8058 one-click), so mail programs
can show their own unsubscribe button.
"""
from django.core import signing
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from forum import settings

SALT = 'forum.unsubscribe'

# scope -> (what the person stops receiving, what the link says)
SCOPES = {
    'all': (_("all emails from this site"), _("Unsubscribe from all emails")),
    'question': (_("emails about this question"), _("Stop emails about this question")),
    'new_questions': (_("emails about new questions, including those in the tags you follow"),
                      _("Stop emails about new questions")),
    'mentions': (_("emails when someone mentions you"), _("Stop emails when someone mentions you")),
    'members': (_("emails about new members"), _("Stop emails about new members")),
    'digest': (_("the daily digest"), _("Stop the daily digest")),
}

# email kind -> (why the person gets it, the specific scope to offer)
KINDS = {
    'question': (_("you follow this question"), 'question'),
    'new_question': (_("you asked to be told about new questions on the site or in the tags you follow"),
                     'new_questions'),
    'mention': (_("someone mentioned you"), 'mentions'),
    'member': (_("you asked to be told when new members join"), 'members'),
    'digest': (_("you are subscribed to the daily digest"), 'digest'),
}


def make_token(user, scope, question=None):
    data = {'u': user.id, 's': scope}
    if question is not None:
        data['q'] = question.id
    return signing.dumps(data, salt=SALT, compress=True)


def read_token(token):
    """(user, scope, question) for a valid token, else None. Tokens don't
    expire: links in old emails must keep working."""
    from forum.models import User, Question
    try:
        data = signing.loads(token, salt=SALT)
        user = User.objects.get(id=data['u'])
    except (signing.BadSignature, KeyError, TypeError, User.DoesNotExist):
        return None
    scope = data.get('s')
    if scope not in SCOPES:
        return None
    question = None
    if scope == 'question':
        question = Question.objects.filter(id=data.get('q')).first()
        if question is None:
            return None
    return user, scope, question


def url(user, scope, question=None):
    path = reverse('email_unsubscribe', kwargs={'token': make_token(user, scope, question)})
    return '%s%s' % (settings.APP_URL, path)


def links(user, kind, question=None):
    """What the footer of a notification email shows to `user`."""
    reason, scope = KINDS[kind]
    data = {
        'reason': reason,
        'all_url': url(user, 'all'),
        'manage_url': '%s%s' % (settings.APP_URL, user.get_user_subscriptions_url()),
        'help_url': '%s%s' % (settings.APP_URL, reverse('email_help')),
    }
    if scope != 'question' or question is not None:
        data['specific_url'] = url(user, scope, question)
        data['specific_label'] = SCOPES[scope][1]
    return data


def apply(user, scope, question=None):
    """Stops what `scope` covers."""
    from forum.models import QuestionSubscription
    prefs = user.subscription_settings
    if scope == 'all':
        # The other settings stay as they are, so "undo" restores them exactly.
        prefs.enable_notifications = False
    elif scope == 'question':
        QuestionSubscription.objects.filter(user=user, question=question).delete()
    elif scope == 'new_questions':
        prefs.new_question = 'n'
        prefs.new_question_watched_tags = 'n'
    elif scope == 'mentions':
        prefs.notify_reply_to_comments = False
    elif scope == 'members':
        prefs.member_joins = 'n'
    elif scope == 'digest':
        prefs.send_digest = False
    prefs.save()


def undo(user, scope, question=None):
    """Turns back on what `scope` covers (the "changed my mind" button)."""
    from forum.models import QuestionSubscription
    prefs = user.subscription_settings
    if scope == 'all':
        prefs.enable_notifications = True
    elif scope == 'question':
        if not QuestionSubscription.objects.filter(user=user, question=question).exists():
            QuestionSubscription(user=user, question=question, auto_subscription=False).save()
    elif scope == 'new_questions':
        prefs.new_question_watched_tags = 'i'
    elif scope == 'mentions':
        prefs.notify_reply_to_comments = True
    elif scope == 'members':
        prefs.member_joins = 'i'
    elif scope == 'digest':
        prefs.send_digest = True
    prefs.save()
