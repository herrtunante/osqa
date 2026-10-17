"""Suggested edits, as on Stack Overflow.

Members who can't edit others' posts yet (below REP_TO_EDIT_OTHERS) may
suggest an edit to a question or answer instead ("Suggest an edit" in the
post's controls, view forum/views/suggested_edits.py). The suggestion
(SuggestedEdit) waits for a reviewer:

- reviewers are members who may edit others' posts (REP_TO_EDIT_OTHERS) and
  the moderators; nobody reviews their own suggestion. They work from the
  public "Review suggested edits" page, moderators also from the moderation
  inbox. Posts held for review or deleted are only listed for moderators.
- one pending suggestion per post at a time; SUGGESTED_EDITS_PER_DAY per
  member in 24 hours (0: no limit).
- approving creates a normal revision credited to the suggester
  (SuggestedEditAction: its user is the suggester, real_user the reviewer,
  who is also named in the revision summary) and gives the suggester
  REP_GAIN_BY_SUGGESTED_EDIT, at most MAX_REP_BY_SUGGESTED_EDITS in all.
  "Improve" approves it and then saves the reviewer's own changes as a
  separate revision. Cancelling the action (undo) takes the reputation back,
  restores the previous revision and puts the suggestion back in the queue.
- the post may have changed since the suggestion was made: changes to other
  fields are kept (the suggestion is applied on top), the same field changed
  both ways is a conflict and the suggestion is superseded (also checked
  right after every edit of the post).
- rejecting needs a reason from REJECT_REASONS (RejectSuggestedEditAction,
  undoable too). The suggester hears the outcome in the forum and by email
  (forum/notifications.py, forum/subscriptions.py).
"""
import datetime

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.template import loader
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from forum import settings
from forum.utils import review

POST_TYPES = ('question', 'answer')
FIELDS = ('title', 'tagnames', 'body')

COUNT_CACHE_KEY = 'suggested-edits-count'
COUNT_CACHE_SECONDS = 60

# key -> what the suggester reads
REJECT_REASONS = (
    ('no_improvement', _lazy("It doesn't make the post easier to read or more useful")),
    ('changes_meaning', _lazy("It changes what the author meant")),
    ('vandalism', _lazy("It damages the post (spam, vandalism or nonsense)")),
    ('comment', _lazy("It should have been a comment or an answer")),
    ('other', _lazy("Another reason (explained below)")),
)
REJECT_REASON_KEYS = tuple(key for key, label in REJECT_REASONS)


def reason_label(key):
    if key == 'conflict':
        return _("The post was edited in the meantime")
    return str(dict(REJECT_REASONS).get(key, key or ''))


def enabled():
    return bool(settings.SUGGESTED_EDITS_ENABLED)


# -- who may do what ------------------------------------------------------------

def is_reviewer(user):
    """Members who may edit others' posts, and the moderators."""
    if user is None or not user.is_authenticated or user.is_suspended():
        return False
    return review.is_moderator(user) or int(user.reputation) >= int(settings.REP_TO_EDIT_OTHERS)


def can_suggest(user, post):
    """Whether `user` sees "Suggest an edit" on `post` (rather than "edit")."""
    if not enabled() or user is None or not user.is_authenticated or user.is_suspended():
        return False
    if post is None or post.node_type not in POST_TYPES or post.nis.deleted:
        return False
    if user.can_edit_post(post) or not review.can_view(post, user):
        return False
    return True


def pending_for(post):
    from forum.models import SuggestedEdit
    return SuggestedEdit.objects.filter(node_id=post.id, status=SuggestedEdit.PENDING).order_by('id').first()


def pending_post_ids(post):
    """Ids of the posts with a pending suggestion on `post`'s question page,
    loaded once per request (post controls ask for every post)."""
    from forum import REQUEST_HOLDER
    from forum.models import SuggestedEdit
    root_id = post.abs_parent_id or post.id
    request = REQUEST_HOLDER.request
    memo = request.__dict__.setdefault('_osqa_suggested_edits', {}) if request is not None else {}
    if root_id not in memo:
        memo[root_id] = set(SuggestedEdit.objects.filter(status=SuggestedEdit.PENDING).filter(
            Q(node_id=root_id) | Q(node__abs_parent_id=root_id)).values_list('node_id', flat=True))
    return memo[root_id]


def suggestions_today(user):
    from forum.models import SuggestedEdit
    since = datetime.datetime.now() - datetime.timedelta(days=1)
    return SuggestedEdit.objects.filter(author_id=user.id, created_at__gte=since).count()


def limit_reached(user):
    limit = int(settings.SUGGESTED_EDITS_PER_DAY or 0)
    return limit > 0 and suggestions_today(user) >= limit


def can_review(user, suggestion):
    """Whether `user` may approve or reject `suggestion`."""
    if not is_reviewer(user) or suggestion.author_id == user.id:
        return False
    post = suggestion.node.leaf
    if review.is_moderator(user):
        return True
    return not post.nis.deleted and review.can_view(post, user) and not review.is_held(post)


# -- the queue --------------------------------------------------------------------

def queue_for(user):
    """The pending suggestions `user` may review, oldest first."""
    from forum.models import SuggestedEdit
    pending = SuggestedEdit.objects.filter(status=SuggestedEdit.PENDING).exclude(author_id=user.id)
    if not review.is_moderator(user):
        # A post under a held question is held with it.
        pending = pending.exclude(review.hidden_q('node__')).exclude(review.held_q('node__abs_parent__'))
    return pending.select_related('node', 'author', 'base_revision').order_by('created_at', 'id')


def pending_count():
    """Pending suggestions on published posts (the reviewers' header link;
    cached for a minute, emptied when one is made or reviewed)."""
    from forum.models import SuggestedEdit
    count = cache.get(COUNT_CACHE_KEY)
    if count is None:
        count = SuggestedEdit.objects.filter(status=SuggestedEdit.PENDING).exclude(
            review.hidden_q('node__')).count()
        cache.set(COUNT_CACHE_KEY, count, COUNT_CACHE_SECONDS)
    return count


def staff_count():
    """Every pending suggestion (the moderation inbox)."""
    from forum.models import SuggestedEdit
    return SuggestedEdit.objects.filter(status=SuggestedEdit.PENDING).count()


def invalidate_counts():
    cache.delete(COUNT_CACHE_KEY)
    review.invalidate_counts()


# -- comparing ------------------------------------------------------------------

def _values(obj):
    return {field: getattr(obj, field) or '' for field in FIELDS}


def proposed_values(suggestion):
    return _values(suggestion)


def merged_values(suggestion):
    """What the post becomes if `suggestion` is approved now: the proposal,
    on top of changes made to other fields since it was made. None when the
    post and the suggestion changed the same field differently."""
    post = suggestion.node.leaf
    current = _values(post.active_revision)
    proposed = proposed_values(suggestion)
    base = _values(suggestion.base_revision) if suggestion.base_revision_id else current
    merged = {}
    for field in FIELDS:
        now, before, new = current[field], base[field], proposed[field]
        if new == before:
            merged[field] = now          # not part of the suggestion
        elif now in (before, new):
            merged[field] = new
        else:
            return None
    return merged


def is_conflicting(suggestion):
    merged = merged_values(suggestion)
    return merged is None or merged == _values(suggestion.node.leaf.active_revision)


def _render(values):
    return loader.get_template('node/revision.html').render({
        'title': values['title'], 'html': values['body'], 'tags': values['tagnames'].split()})


def diff(suggestion, values=None):
    """The inline diff (as on the revisions page) between the revision the
    suggestion started from and the proposal (or `values`)."""
    from forum.utils.diff import textDiff
    post = suggestion.node.leaf
    base = suggestion.base_revision or post.active_revision
    return mark_safe(textDiff(_render(_values(base)), _render(values or proposed_values(suggestion))))


def changed_since(suggestion):
    """Whether the post got a new revision after the suggestion was made."""
    post = suggestion.node.leaf
    return bool(suggestion.base_revision_id and post.active_revision_id != suggestion.base_revision_id)


# -- suggesting -----------------------------------------------------------------------

def create(user, post, data, ip=''):
    """Saves the suggestion from the edit form's cleaned data. Returns it, or
    None when another one is pending for the post."""
    from forum.models import SuggestedEdit
    from django.utils.html import strip_tags
    base = post.active_revision
    with transaction.atomic():
        if SuggestedEdit.objects.select_for_update().filter(node_id=post.id, status=SuggestedEdit.PENDING).exists():
            return None
        suggestion = SuggestedEdit(node=post, author=user, base_revision=base, summary=data['summary'].strip()[:300],
                                   body=data['text'])
        if post.node_type == 'question':
            suggestion.title = strip_tags(data.get('title', '').strip())
            suggestion.tagnames = (data.get('tags') or '').strip()
        else:
            suggestion.title, suggestion.tagnames = base.title or '', base.tagnames or ''
        suggestion.save()
    invalidate_counts()
    return suggestion


def has_changes(post, data):
    current = _values(post.active_revision)
    if data.get('text', '') != current['body']:
        return True
    return post.node_type == 'question' and (data.get('title', '').strip() != current['title'] or
                                             (data.get('tags') or '').strip() != current['tagnames'])


# -- reviewing --------------------------------------------------------------------------

def rep_gain_for(user):
    """The reputation an approved suggestion gives `user` now (capped)."""
    from forum.models import ActionRepute
    gain = int(settings.REP_GAIN_BY_SUGGESTED_EDIT or 0)
    cap = int(settings.MAX_REP_BY_SUGGESTED_EDITS or 0)
    if gain <= 0 or cap <= 0:
        return max(gain, 0)
    earned = ActionRepute.objects.filter(user_id=user.id, action__action_type='suggestededit').aggregate(
        total=Sum('value'))['total'] or 0
    return max(0, min(gain, cap - earned))


def supersede(suggestion, reason='conflict'):
    from forum.models import SuggestedEdit
    updated = SuggestedEdit.objects.filter(id=suggestion.id, status=SuggestedEdit.PENDING).update(
        status=SuggestedEdit.SUPERSEDED, reason=reason, reviewed_at=datetime.datetime.now())
    suggestion.status = SuggestedEdit.SUPERSEDED
    invalidate_counts()
    return bool(updated)


def approve(suggestion, reviewer, ip=''):
    """Approves `suggestion`: returns the SuggestedEditAction, or None when it
    is no longer pending or was superseded (conflicting edit)."""
    from forum.actions import SuggestedEditAction
    from forum.models import SuggestedEdit
    with transaction.atomic():
        suggestion = SuggestedEdit.objects.select_for_update().get(id=suggestion.id)
        if suggestion.status != SuggestedEdit.PENDING:
            return None
        if is_conflicting(suggestion):
            supersede(suggestion)
            return None
        merged = merged_values(suggestion)
        post = suggestion.node.leaf
        summary = _("%(summary)s (suggested edit approved by %(reviewer)s)") % {
            'summary': suggestion.summary, 'reviewer': reviewer.username}
        action = SuggestedEditAction(user=suggestion.author, real_user=reviewer, node=post, ip=ip)
        action._suggestion = suggestion
        action.save(data=dict(text=merged['body'], title=merged['title'] if post.node_type == 'question' else '',
                              tags=merged['tagnames'] if post.node_type == 'question' else '',
                              summary=summary[:300]))
    invalidate_counts()
    return action


def reject(suggestion, reviewer, reason, comment='', ip=''):
    """Rejects `suggestion` (reason: a REJECT_REASONS key). Returns the
    RejectSuggestedEditAction, or None when it is no longer pending."""
    from forum.actions import RejectSuggestedEditAction
    from forum.models import SuggestedEdit
    with transaction.atomic():
        suggestion = SuggestedEdit.objects.select_for_update().get(id=suggestion.id)
        if suggestion.status != SuggestedEdit.PENDING:
            return None
        action = RejectSuggestedEditAction(user=reviewer, node=suggestion.node.leaf, ip=ip, extra={
            'suggestion': suggestion.id, 'reason': reason, 'comment': (comment or '').strip()[:300]})
        action._suggestion = suggestion
        action.save()
    invalidate_counts()
    return action


def check_after_edit(node):
    """After an edit of `node`: a pending suggestion it conflicts with is superseded."""
    suggestion = pending_for(node)
    if suggestion is not None and is_conflicting(suggestion):
        supersede(suggestion)


def reopen(suggestion):
    """Undo of a review: back to the queue, unless another suggestion is pending by now."""
    from forum.models import SuggestedEdit
    other = SuggestedEdit.objects.filter(node_id=suggestion.node_id, status=SuggestedEdit.PENDING).exclude(
        id=suggestion.id).exists()
    suggestion.status = SuggestedEdit.SUPERSEDED if other else SuggestedEdit.PENDING
    suggestion.reviewer = None
    suggestion.reviewed_at = None
    suggestion.reason = '' if not other else 'conflict'
    suggestion.comment = ''
    suggestion.action = None
    suggestion.save()
    invalidate_counts()
