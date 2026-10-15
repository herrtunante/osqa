"""What the author of a post held for review hears (forum/utils/review.py):
an email and an in-site notification when a moderator approves it or removes
it. Also keeps the moderation inbox count in the header fresh."""
from forum.actions import ApprovePostAction, DeleteAction, FlagAction, ReportAction
from forum.models import Notification
from forum.utils import review
from forum.utils.mail import send_template_email


def tell_author(node, outcome, moderator):
    author = node.author
    if author is None or author.id == getattr(moderator, 'id', None) or not author.is_active:
        return
    from forum.notifications import notify
    notify({author.id}, Notification.APPROVED if outcome == 'approved' else Notification.REMOVED, moderator, node)
    send_template_email([author], 'notifications/post_reviewed.html', {'node': node, 'outcome': outcome})


def post_approved(action, new):
    review.invalidate_counts()
    # Posts held under an approved one are approved with it (cascade): one message is enough.
    if action.was_held and not action.node.nis.deleted and not getattr(action, 'cascade', False):
        tell_author(action.node, 'approved', action.user)

ApprovePostAction.hook(post_approved)


def post_deleted(action, new):
    review.invalidate_counts()
    node = action.node
    if review.is_held(node) and action.user_id != node.author_id and not getattr(action, 'quiet', False):
        tell_author(node, 'removed', action.user)

DeleteAction.hook(post_deleted)

FlagAction.hook(review.invalidate_counts)
ReportAction.hook(review.invalidate_counts)
