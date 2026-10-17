"""The first posts review queue (FIRST_POSTS_MODERATION) and who may see a
post held for review.

A new member's first posts (FIRST_POSTS_REVIEW_COUNT; also every post by a
member below FIRST_POSTS_REVIEW_MIN_REP) get a node state, like 'deleted':

- 'pending_review' ("before" mode): held. Nobody but its author and the
  moderators sees it until a moderator approves it; followers are notified
  then (Node.is_notifiable is False while it is held). A post under a held
  post is held with it.
- 'unreviewed' ("after" mode): published at once, and listed in the
  moderation inbox until a moderator approves it.

The state's action is a QueueForReviewAction; approving is an
ApprovePostAction (forum/actions/node.py), which can be undone.

Every place that shows posts to other people goes through this module:
`published()` / `PUBLISHED` for what everybody sees (feeds, sitemap, search
index, exports, related questions, AI), `visible_to()` / `can_view()` for
pages that depend on who is looking (lists, question page, profiles).
"""
from django.core.cache import cache
from django.db.models import Q

HELD = 'pending_review'
UNREVIEWED = 'unreviewed'
QUEUED_STATES = (HELD, UNREVIEWED)

HELD_MARK = '(%s)' % HELD
UNREVIEWED_MARK = '(%s)' % UNREVIEWED
DELETED_MARK = '(deleted)'

POST_TYPES = ('question', 'answer', 'comment')

COUNTS_CACHE_KEY = 'moderation-inbox-counts'
COUNTS_CACHE_SECONDS = 60


def held_q(prefix=''):
    """Q matching posts held for review (`prefix` e.g. 'node__' for a relation)."""
    return Q(**{prefix + 'state_string__contains': HELD_MARK})


def hidden_q(prefix=''):
    """Q matching posts nobody but their author and moderators sees: deleted or held."""
    return Q(**{prefix + 'state_string__contains': DELETED_MARK}) | held_q(prefix)


def queued_q(prefix=''):
    """Q matching posts listed in the review queue (held or unreviewed)."""
    return held_q(prefix) | Q(**{prefix + 'state_string__contains': UNREVIEWED_MARK})


def is_moderator(user):
    return bool(user is not None and user.is_authenticated and (user.is_staff or user.is_superuser))


def published(queryset, prefix=''):
    """`queryset` (of nodes, or of rows related to nodes through `prefix`)
    without the posts held for review: what everybody may see."""
    return queryset.exclude(held_q(prefix))


def visible_to(queryset, user, prefix=''):
    """`queryset` without the held posts `user` may not see: moderators see
    them all, members their own, anonymous visitors none."""
    if is_moderator(user):
        return queryset
    if user is not None and user.is_authenticated:
        return queryset.exclude(held_q(prefix) & ~Q(**{prefix + 'author_id': user.id}))
    return queryset.exclude(held_q(prefix))


def is_held(node):
    return node is not None and HELD_MARK in (node.state_string or '')


def is_queued(node):
    state = (node.state_string or '') if node is not None else ''
    return HELD_MARK in state or UNREVIEWED_MARK in state


def can_view(node, user):
    """Whether `user` may see `node` as far as the review is concerned (the
    post itself and the question it belongs to)."""
    for post in (node, getattr(node, 'abs_parent', None) if node is not None and node.abs_parent_id else None):
        if is_held(post) and not (is_moderator(user) or (user is not None and user.is_authenticated
                                                          and post.author_id == user.id)):
            return False
    return True


# -- which new posts are reviewed ---------------------------------------------

def mode():
    from forum import settings
    value = str(settings.FIRST_POSTS_MODERATION or 'off')
    return value if value in ('off', 'after', 'before') else 'off'


def approved_post_count(user, limit):
    """How many of `user`'s posts are published and reviewed (not deleted,
    not waiting for review), counting at most `limit`."""
    from forum.models import Node
    if limit <= 0:
        return 0
    ids = Node.objects.filter(author_id=user.id, node_type__in=POST_TYPES).exclude(
        queued_q() | Q(state_string__contains=DELETED_MARK)).values_list('id', flat=True)[:limit]
    return len(list(ids))


def needs_review(user):
    """Whether a new post by `user` goes to the review queue (whatever the mode)."""
    from forum import settings
    if user is None or not user.is_authenticated or is_moderator(user):
        return False
    min_rep = int(settings.FIRST_POSTS_REVIEW_MIN_REP or 0)
    if min_rep > 0 and int(user.reputation) < min_rep:
        return True
    count = int(settings.FIRST_POSTS_REVIEW_COUNT or 0)
    return count > 0 and approved_post_count(user, count) < count


def state_for_new_post(user, parent=None):
    """The review state a new post by `user` (under `parent`, for answers and
    comments) starts in: HELD, UNREVIEWED or None (published)."""
    if parent is not None and (is_held(parent) or (parent.abs_parent_id and is_held(parent.abs_parent))):
        return HELD
    current = mode()
    if current == 'off' or not needs_review(user):
        return None
    return HELD if current == 'before' else UNREVIEWED


def state_mark(state):
    """The state_string a new post starts with."""
    return '(%s)' % state if state else ''


def queue_post(post_action, state):
    """Records why the new post of `post_action` (an Ask/Answer/CommentAction)
    is in the queue; the node already has the state in its state_string."""
    if not state:
        return None
    from forum.actions import QueueForReviewAction
    action = QueueForReviewAction(user=post_action.user, node=post_action.node, ip=post_action.ip or '',
                                  extra=state)
    action.save()
    setattr(post_action.node.nstate, state, action)
    invalidate_counts()
    return action


def held_notice():
    from django.utils.translation import gettext as _
    return _("Thanks! Your post will appear once a moderator has reviewed it.")


# -- releasing and holding ----------------------------------------------------

def release(node):
    """Takes `node` out of the queue (approval): clears its state and counts
    its tags. Returns the posts that were held under it (to approve with it)."""
    was_held = is_held(node)
    for state in QUEUED_STATES:
        _clear_state(node, state)
    if was_held:
        _count_tags(node, 1)
    _reset_counts(node)
    invalidate_counts()
    if not was_held:
        return []
    from forum.models import Node
    children = Node.objects.filter(abs_parent=node) if node.node_type == 'question' else Node.objects.filter(parent=node)
    return [child.leaf for child in children.filter(held_q()).exclude(id=node.id).order_by('id')]


def _clear_state(node, state):
    mark = '(%s)' % state
    if mark not in (node.state_string or ''):
        return
    setattr(node.nstate, state, None)
    if mark in (node.state_string or ''):
        # No NodeState row behind the mark (e.g. an interrupted save): drop the mark itself.
        node.state_string = node.state_string.replace(mark, '')
        node.save()


def hold_again(node, state):
    """Undoing an approval: back to the queue in `state`."""
    from forum.actions import QueueForReviewAction
    queue_action = QueueForReviewAction.objects.filter(node=node, canceled=False).order_by('-id').first()
    if queue_action is None or getattr(node.nis, state):
        return
    setattr(node.nstate, state, queue_action)
    if state == HELD:
        _count_tags(node, -1)
    _reset_counts(node)
    invalidate_counts()


def _count_tags(node, delta):
    from forum.models import Tag
    if node.node_type != 'question' or node.nis.deleted:
        return
    for tag in Tag.objects.filter(name__in=node.tagname_list()):
        tag.add_to_usage_count(delta)
        tag.save()


def _reset_counts(node):
    if node.node_type == 'answer' and node.parent_id:
        question = node.parent
        question.reset_answer_count_cache()
        question.reset_accepted_count_cache()


# -- the moderation inbox -----------------------------------------------------

def review_queue():
    """Posts waiting for review, oldest first (deleted ones are done)."""
    from forum.models import Node
    return Node.objects.filter(node_type__in=POST_TYPES).filter(queued_q()).exclude(
        state_string__contains=DELETED_MARK).order_by('added_at', 'id')


def open_flags():
    """Flag actions not dismissed, on posts that aren't deleted."""
    from forum.models import Action
    return Action.objects.filter(action_type='flag', canceled=False).exclude(
        node__state_string__contains=DELETED_MARK)


def open_user_reports():
    """{user id: [ReportAction, ...]} for the "report user" actions not dismissed."""
    from forum.models import Action
    reports = {}
    for action in Action.objects.filter(action_type='report', canceled=False).order_by('-action_date')[:500]:
        action = action.leaf
        reported = (action.extra or {}).get('reported') if isinstance(action.extra, dict) else None
        if reported is not None and getattr(reported, 'id', None):
            reports.setdefault(reported.id, []).append(action)
    return reports


def inbox_counts():
    """{'review', 'flags', 'ai', 'users', 'edits', 'total'}: what waits in the moderation
    inbox (cached for a minute, emptied when a moderator acts)."""
    counts = cache.get(COUNTS_CACHE_KEY)
    if counts is None:
        from forum.models import ModerationReport
        counts = {
            'review': review_queue().count(),
            'flags': open_flags().values('node_id').distinct().count(),
            'ai': ModerationReport.objects.filter(resolved_at=None).count(),
            'users': len(open_user_reports()),
        }
        from forum.utils import suggested_edits
        counts['edits'] = suggested_edits.staff_count()
        counts['total'] = sum(counts.values())
        cache.set(COUNTS_CACHE_KEY, counts, COUNTS_CACHE_SECONDS)
    return counts


def invalidate_counts(*args, **kwargs):
    cache.delete(COUNTS_CACHE_KEY)


def posting_action(node):
    """The Ask/Answer/CommentAction that created `node` (its leaf class), or None."""
    action = node.actions.filter(action_type__in=('ask', 'answer', 'comment'), canceled=False).order_by('id').first()
    return action.leaf if action is not None else None


def replay_on_approval(approve_action, hooks):
    """For an ApprovePostAction of a held post: runs the hook in `hooks`
    ({'ask': fn, 'answer': fn, 'comment': fn}) of the action that created the
    post, as it ran (and sent nothing) when the post was held."""
    if not getattr(approve_action, 'was_held', False):
        return
    post_action = posting_action(approve_action.node)
    hook = hooks.get(post_action.action_type) if post_action is not None else None
    if hook is not None:
        hook(action=post_action, new=True)
