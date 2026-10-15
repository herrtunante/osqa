"""The daily digest (run from cron, e.g. once a day).

Each run covers what happened since the previous run (stored in the
EMAIL_DIGEST_FLAG setting as LAST_DAILY), so running it twice sends nothing
new the second time. There is no weekly digest: users only choose whether
they get the daily one (SubscriptionSettings.send_digest).
"""
import datetime
import logging

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import translation

from forum import settings
from forum.models import Flag, Question, QuestionSubscription, Tag, User
from forum.settings.email import EMAIL_DIGEST_FLAG
from forum.utils.mail import send_template_email

SHOW_N_MORE_ACTIVE_NEW_MEMBERS = 5
SUB_QUESTION_LIST_LENGTH = 5
TRY_N_USER_TAGS = 5


class DigestQuestionsIndex:
    def __init__(self, from_date):
        self.from_date = from_date

        new_questions = Question.objects.filter_state(deleted=False).\
            filter(added_at__gt=from_date).\
            annotate(n_actions=models.Count('actions', distinct=True)).\
            annotate(child_count=models.Count('all_children', distinct=True))

        hotness = lambda q: 3*q.child_count + q.n_actions

        for q in new_questions:
            q.hotness = hotness(q)

        self.questions = sorted(new_questions, key=lambda q: q.hotness, reverse=True)
        self.count = len(self.questions)

    def unseen_question(self, user, question):
        subscription = QuestionSubscription.objects.filter(question=question, user=user).first()
        return (not subscription) or subscription.last_view < question.last_activity_at

    def get_for_user(self, user):
        user_tags = list(user.marked_tags.filter(user_selections__reason='good'))

        if len(user_tags) < TRY_N_USER_TAGS:
            user_tags += list(Tag.objects.filter(models.Q(nodes__author=user) | models.Q(nodes__children__author=user)) \
                .annotate(user_tag_usage_count=models.Count('name')).order_by('-user_tag_usage_count')[:TRY_N_USER_TAGS - len(user_tags)])

        user_tag_names = set([t.name for t in user_tags])

        subscriptions = user.subscriptions.filter(added_at__lt=self.from_date, last_activity_at__gt=models.F('questionsubscription__last_view')
                                                  ).order_by('-questionsubscription__last_view')[:SUB_QUESTION_LIST_LENGTH]

        unseen_questions = [q for q in self.questions if self.unseen_question(user, q)]

        interesting = []

        for q in unseen_questions:
            if len(set(q.tagname_list()) & user_tag_names): interesting.append(q)

        may_help = []
        if len(interesting):
            if len(interesting) > SUB_QUESTION_LIST_LENGTH:
                may_help = interesting[SUB_QUESTION_LIST_LENGTH:][-SUB_QUESTION_LIST_LENGTH:]
                interesting = interesting[:SUB_QUESTION_LIST_LENGTH]
        else:
            interesting = unseen_questions[:SUB_QUESTION_LIST_LENGTH]

        return {'interesting': interesting, 'may_help': may_help, 'subscriptions': subscriptions}


class Command(BaseCommand):
    help = "Sends the daily digest email to the users who want it."

    def handle(self, *args, **options):
        try:
            translation.activate(settings.LANGUAGE_CODE)
        except Exception:
            logging.error("Unable to set the locale in the send emails cron job")

        now = datetime.datetime.now()
        digest_control = EMAIL_DIGEST_FLAG.value
        if not isinstance(digest_control, dict) or 'LAST_DAILY' not in digest_control:
            digest_control = {'LAST_DAILY': now - datetime.timedelta(days=1)}

        from_date = digest_control['LAST_DAILY']
        digest_control = dict(digest_control, LAST_DAILY=now)
        EMAIL_DIGEST_FLAG.set_value(digest_control)

        users = User.objects.filter(subscription_settings__enable_notifications=True,
                                    subscription_settings__send_digest=True)

        # Send digest only to active users
        if settings.SEND_DIGEST_ONLY_TO_ACTIVE_USERS:
            users = users.filter(is_active=True)

        # Send digest only to users with validated emails
        if settings.SEND_DIGEST_ONLY_TO_VALIDATED_USERS:
            users = users.filter(email_isvalid=True)

        new_members = User.objects.filter(is_active=True, date_joined__gt=from_date).annotate(
            n_actions=models.Count('actions')).order_by('-n_actions')

        new_member_count = new_members.count()

        # The number of the flagged content for the day
        flagged_count = Flag.objects.filter(flagged_at__gt=now - datetime.timedelta(days=1)).count()

        if new_member_count >= SHOW_N_MORE_ACTIVE_NEW_MEMBERS:
            new_members = new_members[:SHOW_N_MORE_ACTIVE_NEW_MEMBERS]
            show_all_users = True
        else:
            show_all_users = False

        digest = DigestQuestionsIndex(from_date)

        if (not new_member_count) and (not digest.count):
            return

        send_template_email(users, "notifications/digest.html", {
            'digest': digest, 'new_members': list(new_members), 'new_member_count': new_member_count,
            'show_all_users': show_all_users, 'flagged_count': flagged_count, 'email_kind': 'digest',
        })
