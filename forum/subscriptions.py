import os
import re
import datetime
import logging
from forum.models import User, Question, Comment, QuestionSubscription, SubscriptionSettings, Answer
from forum.utils.mail import send_template_email
from django.utils.translation import gettext as _
from forum.actions import AskAction, AnswerAction, CommentAction, AcceptAnswerAction, UserJoinsAction, QuestionViewAction, ReviseAction
from forum.utils.mentions import mentioned_user_ids
from forum import settings
from django.db.models import Q, F

def create_subscription_if_not_exists(question, user):
    try:
        subscription = QuestionSubscription.objects.get(question=question, user=user)
        return subscription
    except QuestionSubscription.MultipleObjectsReturned:
        pass
    except QuestionSubscription.DoesNotExist:
        subscription = QuestionSubscription(question=question, user=user)
        subscription.save()
        return subscription
    except Exception as e:
        logging.error(e)

    return False

def filter_subscribers(subscribers):
    subscribers = subscribers.exclude(is_active=False)

    if settings.DONT_NOTIFY_UNVALIDATED:
        return subscribers.exclude(email_isvalid=False)
    else:
        return subscribers

def notify_mentioned(node, author, user_ids):
    """Email the users mentioned (@username) in a post or comment. Returns their ids."""
    user_ids = set(user_ids) - {author.id}
    if not user_ids or not node.is_notifiable:
        return set()

    question = node.leaf.absolute_parent
    if not question.is_notifiable:
        return set()

    users = filter_subscribers(User.objects.filter(
        id__in=user_ids,
        subscription_settings__enable_notifications=True,
        subscription_settings__notify_reply_to_comments=True,
    ))
    send_template_email(users, "notifications/mention.html",
                        {'node': node.leaf, 'question': question, 'author': author})
    return user_ids


def question_posted(action, new):
    question = action.node

    if not question.is_notifiable:
        return

    mentioned = notify_mentioned(question, question.author, mentioned_user_ids(question.body))

    subscribers = User.objects.filter(
            Q(subscription_settings__enable_notifications=True, subscription_settings__new_question='i') |
            (Q(subscription_settings__new_question_watched_tags='i') &
              Q(marked_tags__name__in=question.tagnames.split(' ')) &
              Q(tag_selections__reason='good'))
    ).exclude(id=question.author.id).exclude(id__in=mentioned).distinct()

    subscribers = filter_subscribers(subscribers)

    send_template_email(subscribers, "notifications/newquestion.html", {'question': question})

    subscription = QuestionSubscription(question=question, user=question.author)
    subscription.save()

    new_subscribers = User.objects.filter(
            Q(subscription_settings__all_questions=True) |
            Q(subscription_settings__all_questions_watched_tags=True,
                    marked_tags__name__in=question.tagnames.split(' '),
                    tag_selections__reason='good'))

    for user in new_subscribers:
        create_subscription_if_not_exists(question, user)

AskAction.hook(question_posted)


def answer_posted(action, new):
    answer = action.node
    question = answer.question

    if not answer.is_notifiable or not question.is_notifiable:
        return

    mentioned = notify_mentioned(answer, answer.author, mentioned_user_ids(answer.body))

    subscribers = question.subscribers.filter(
            subscription_settings__enable_notifications=True,
            subscription_settings__notify_answers=True,
            subscription_settings__subscribed_questions='i'
    ).exclude(id=answer.author.id).exclude(id__in=mentioned).distinct()

    subscribers = filter_subscribers(subscribers)

    send_template_email(subscribers, "notifications/newanswer.html", {'answer': answer})

    create_subscription_if_not_exists(question, answer.author)

AnswerAction.hook(answer_posted)


def comment_posted(action, new):
    comment = action.node
    post = comment.parent

    if not comment.is_notifiable or not post.is_notifiable:
        return

    if post.__class__ == Question:
        question = post
    else:
        question = post.question

    q_filter = Q(subscription_settings__notify_comments=True) | Q(subscription_settings__notify_comments_own_post=True, id=post.author.id)

    # Replies use @username: the people mentioned get a "mentioned you" email instead.
    mentioned = notify_mentioned(comment, comment.user, mentioned_user_ids(comment.body))

    subscribers = question.subscribers.filter(
            q_filter, subscription_settings__subscribed_questions='i', subscription_settings__enable_notifications=True
    ).exclude(id=comment.user.id).exclude(id__in=mentioned).distinct()

    subscribers = filter_subscribers(subscribers)


    send_template_email(subscribers, "notifications/newcomment.html", {'comment': comment})

    create_subscription_if_not_exists(question, comment.user)

CommentAction.hook(comment_posted)


def post_edited(action, new):
    """An edit notifies only the people it newly mentions."""
    node = action.node.leaf
    try:
        previous = node.revisions.get(revision=int(action.extra) - 1)
        before = mentioned_user_ids(node.rendered(previous.body))
    except Exception:
        before = set()
    notify_mentioned(node, action.user, mentioned_user_ids(node.body) - before)

ReviseAction.hook(post_edited)


def answer_accepted(action, new):
    question = action.node.question

    if not question.is_notifiable:
        return

    subscribers = question.subscribers.filter(
            subscription_settings__enable_notifications=True,
            subscription_settings__subscribed_questions='i'
    ).exclude(id=action.node.nstate.accepted.by.id).distinct()
    
    subscribers = filter_subscribers(subscribers)

    send_template_email(subscribers, "notifications/answeraccepted.html", {'answer': action.node})

AcceptAnswerAction.hook(answer_accepted)


def member_joined(action, new):
    subscribers = User.objects.filter(
            subscription_settings__enable_notifications=True,
            subscription_settings__member_joins='i'
    ).exclude(id=action.user.id).distinct()

    subscribers = filter_subscribers(subscribers)

    send_template_email(subscribers, "notifications/newmember.html", {'newmember': action.user})

UserJoinsAction.hook(member_joined)

def question_viewed(action, new):
    if not action.viewuser.is_authenticated:
        return

    try:
        subscription = QuestionSubscription.objects.get(question=action.node, user=action.viewuser)
        subscription.last_view = datetime.datetime.now()
        subscription.save()
    except:
        if action.viewuser.subscription_settings.questions_viewed:
            subscription = QuestionSubscription(question=action.node, user=action.viewuser)
            subscription.save()

QuestionViewAction.hook(question_viewed)


#todo: translate this
#record_answer_event_re = re.compile("You have received (a|\d+) .*new response.*")
#def record_answer_event(instance, created, **kwargs):
#    if created:
#        q_author = instance.question.author
#        found_match = False
#        #print 'going through %d messages' % q_author.message_set.all().count()
#        for m in q_author.message_set.all():
##            #print m.message
# #           match = record_answer_event_re.search(m.message)
#            if match:
#                found_match = True
#                try:
#                    cnt = int(match.group(1))
#                except:
#                    cnt = 1
##                m.message = u"You have received %d <a href=\"%s?sort=responses\">new responses</a>."\
# #                           % (cnt+1, q_author.get_profile_url())
#
#                m.save()
#                break
#        if not found_match:
#            msg = u"You have received a <a href=\"%s?sort=responses\">new response</a>."\
#                    % q_author.get_profile_url()
#
#            q_author.message_set.create(message=msg)
#
#post_save.connect(record_answer_event, sender=Answer)
