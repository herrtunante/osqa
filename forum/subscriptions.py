import datetime
import logging
from forum.models import User, Question, QuestionSubscription, PendingEmail
# Activity emails go through deliver(): now, queued for a daily/weekly summary, or not
# at all, per member (SubscriptionSettings.email_frequency).
from forum.utils.email_frequency import deliver
from forum.actions import AskAction, AnswerAction, CommentAction, AcceptAnswerAction, UserJoinsAction, QuestionViewAction, ReviseAction
from forum.utils.mentions import mentioned_user_ids
from forum import settings
from django.db.models import Q

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
    deliver(users, PendingEmail.MENTION, "notifications/mention.html",
            {'node': node.leaf, 'question': question, 'author': author, 'email_kind': 'mention'},
            node=node, actor=author)
    return user_ids


def question_posted(action, new):
    question = action.node

    if not question.is_notifiable:
        return

    mentioned = notify_mentioned(question, question.author, mentioned_user_ids(question.body))

    # Everyone who wants all new questions, or new questions in a tag they follow;
    # the master switch (enable_notifications) applies to both.
    subscribers = User.objects.filter(subscription_settings__enable_notifications=True).filter(
            Q(subscription_settings__new_question='i') |
            Q(subscription_settings__new_question_watched_tags='i',
              tag_selections__tag__name__in=question.tagnames.split(' '),
              tag_selections__reason='good')
    ).exclude(id=question.author.id).exclude(id__in=mentioned).distinct()

    subscribers = filter_subscribers(subscribers)

    deliver(subscribers, PendingEmail.NEW_QUESTION, "notifications/newquestion.html",
            {'question': question, 'email_kind': 'new_question'}, node=question, actor=question.author)

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

    deliver(subscribers, PendingEmail.ANSWER, "notifications/newanswer.html",
            {'answer': answer, 'email_kind': 'question', 'email_question': answer.question},
            node=answer, actor=answer.author)

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


    deliver(subscribers, PendingEmail.COMMENT, "notifications/newcomment.html",
            {'comment': comment, 'email_kind': 'question', 'email_question': comment.absolute_parent},
            node=comment, actor=comment.user)

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

    # Followers who asked for it ("An answer being accepted" in the email
    # settings), and the author of the answer, for whom it's news about their own post.
    answer = action.node
    subscribers = question.subscribers.filter(
            Q(subscription_settings__notify_accepted=True) | Q(id=answer.author.id),
            subscription_settings__enable_notifications=True,
            subscription_settings__subscribed_questions='i'
    ).exclude(id=answer.nstate.accepted.by.id).distinct()

    subscribers = filter_subscribers(subscribers)

    deliver(subscribers, PendingEmail.ACCEPTED, "notifications/answeraccepted.html",
            {'answer': action.node, 'accepted_by': answer.nstate.accepted.by, 'email_kind': 'question',
             'email_question': action.node.question},
            node=answer, actor=answer.nstate.accepted.by)

AcceptAnswerAction.hook(answer_accepted)


def member_joined(action, new):
    subscribers = User.objects.filter(
            subscription_settings__enable_notifications=True,
            subscription_settings__member_joins='i'
    ).exclude(id=action.user.id).distinct()

    subscribers = filter_subscribers(subscribers)

    deliver(subscribers, PendingEmail.MEMBER, "notifications/newmember.html",
            {'newmember': action.user, 'email_kind': 'member'}, actor=action.user)

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


# First posts review (forum/utils/review.py): a post held for review notified
# nobody (Node.is_notifiable is False while it is held). When a moderator
# approves it, send the emails its posting would have sent.
def post_approved(action, new):
    from forum.utils.review import replay_on_approval
    replay_on_approval(action, {'ask': question_posted, 'answer': answer_posted, 'comment': comment_posted})

from forum.actions import ApprovePostAction  # noqa: E402
ApprovePostAction.hook(post_approved)



# Suggested edits (forum/utils/suggested_edits.py): the member who suggested
# an edit hears whether it was approved or rejected (and why), as often as
# they want activity emails.
def suggested_edit_reviewed(action, new):
    from forum.models import SuggestedEdit
    from forum.utils.suggested_edits import reason_label
    approved = isinstance(action, SuggestedEditAction)
    if approved:
        suggestion = SuggestedEdit.objects.filter(action_id=action.id).first()
        reviewer = action.real_user
    else:
        extra = action.extra if isinstance(action.extra, dict) else {}
        suggestion = SuggestedEdit.objects.filter(id=extra.get('suggestion')).first()
        reviewer = action.user
    if suggestion is None or reviewer is None:
        return
    users = filter_subscribers(User.objects.filter(id=suggestion.author_id,
                                                   subscription_settings__enable_notifications=True))
    post = action.node.leaf
    deliver(users, PendingEmail.EDIT_APPROVED if approved else PendingEmail.EDIT_REJECTED,
            "notifications/suggested_edit.html",
            {'suggestion': suggestion, 'node': post, 'reviewer': reviewer,
             'outcome': 'approved' if approved else 'rejected', 'reason': reason_label(suggestion.reason),
             'email_kind': 'suggested_edit'},
            node=post, actor=reviewer)

from forum.actions import SuggestedEditAction, RejectSuggestedEditAction  # noqa: E402
SuggestedEditAction.hook(suggested_edit_reviewed)
RejectSuggestedEditAction.hook(suggested_edit_reviewed)
