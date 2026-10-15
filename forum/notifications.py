"""In-site notifications (the bell in the header).

Created from the same actions as the notification emails (forum/subscriptions.py),
but independent of the email preferences: someone who turned emails off still
sees replies to their posts in the forum.

- new answer: the asker and the question's followers
- new comment: the author of the commented post and the asker
- mention (@username): the people mentioned, in a post, comment or edit
- accepted answer: the answer's author
- badge: the person awarded
- post approved / removed: the author of a post held for review (forum/review_notices.py)
"""
import datetime

from forum.actions import AnswerAction, CommentAction, AcceptAnswerAction, AskAction, ReviseAction, AwardAction, \
    ApprovePostAction
from forum.models import Notification, User, Award
from forum.utils.mentions import mentioned_user_ids

# Read notifications older than this are deleted when their owner opens the list.
KEEP_READ_DAYS = 90


def notify(user_ids, kind, actor=None, node=None, text=''):
    """Creates one notification per active user in `user_ids` (the actor excluded)."""
    user_ids = set(user_ids) - {getattr(actor, 'id', None), None}
    if not user_ids:
        return []
    users = User.objects.filter(id__in=user_ids, is_active=True).values_list('id', flat=True)
    return Notification.objects.bulk_create([
        Notification(user_id=user_id, kind=kind, actor=actor, node=node, text=text[:255]) for user_id in users])


def unread_count(user):
    if not user.is_authenticated:
        return 0
    return Notification.objects.filter(user=user, read_at=None).count()


def mark_read(user, **filters):
    return Notification.objects.filter(user=user, read_at=None, **filters).update(read_at=datetime.datetime.now())


def mark_question_read(user, question):
    """Opening a question marks the notifications about it (and its answers and comments) as read."""
    if user.is_authenticated:
        from django.db.models import Q
        Notification.objects.filter(Q(node=question) | Q(node__abs_parent=question), user=user, read_at=None) \
            .update(read_at=datetime.datetime.now())


def prune(user):
    cutoff = datetime.datetime.now() - datetime.timedelta(days=KEEP_READ_DAYS)
    Notification.objects.filter(user=user, read_at__lt=cutoff).delete()


def question_asked(action, new):
    question = action.node
    if question.is_notifiable:
        notify(mentioned_user_ids(question.body), Notification.MENTION, question.author, question)

AskAction.hook(question_asked)


def answer_posted(action, new):
    answer = action.node
    question = answer.question
    if not answer.is_notifiable or not question.is_notifiable:
        return
    mentioned = mentioned_user_ids(answer.body)
    notify(mentioned, Notification.MENTION, answer.author, answer)
    followers = set(question.subscribers.values_list('id', flat=True)) | {question.author_id}
    notify(followers - set(mentioned), Notification.ANSWER, answer.author, answer)

AnswerAction.hook(answer_posted)


def comment_posted(action, new):
    comment = action.node
    post = comment.parent
    if not comment.is_notifiable or not post.is_notifiable:
        return
    question = comment.absolute_parent
    mentioned = mentioned_user_ids(comment.body)
    notify(mentioned, Notification.MENTION, comment.author, comment)
    notify({post.author_id, question.author_id} - set(mentioned), Notification.COMMENT, comment.author, comment)

CommentAction.hook(comment_posted)


def post_edited(action, new):
    """An edit notifies only the people it newly mentions."""
    node = action.node.leaf
    try:
        previous = node.revisions.get(revision=int(action.extra) - 1)
        before = mentioned_user_ids(node.rendered(previous.body))
    except Exception:
        before = set()
    if node.is_notifiable:
        notify(mentioned_user_ids(node.body) - before, Notification.MENTION, action.user, node)

ReviseAction.hook(post_edited)


def answer_accepted(action, new):
    answer = action.node
    if answer.is_notifiable:
        notify({answer.author_id}, Notification.ACCEPTED, action.user, answer)

AcceptAnswerAction.hook(answer_accepted)


def badge_awarded(action, new):
    award = Award.objects.filter(action=action).select_related('badge').first()
    if award is not None:
        notify({action.user_id}, Notification.BADGE, None, action.node, text=str(award.badge.name))

AwardAction.hook(badge_awarded)


def post_approved(action, new):
    """A post held for review was approved: the notifications held back are
    created now (see forum/utils/review.py)."""
    from forum.utils.review import replay_on_approval
    replay_on_approval(action, {'ask': question_asked, 'answer': answer_posted, 'comment': comment_posted})

ApprovePostAction.hook(post_approved)
