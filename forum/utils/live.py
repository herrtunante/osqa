"""The live question page (media/js/osqa.live.js) and edit conflicts.

Polling
-------
While a question page is open its script asks GET /questions/<id>/updates/
(forum.views.live.question_updates) every POLL_SECONDS while the tab is
visible, with the `since` token the page was rendered with (milliseconds
since the epoch, server clock) and the ETag of its last answer in
If-None-Match. The answer is a snapshot of what the visitor may see: for the
question, each visible answer and each visible comment its id, revision
(active_revision_id), score, and for answers whether it is accepted and
whether it was added after `since`. The script compares it with the page:
answers it doesn't show and added after `since` and edited posts go in the
"2 new answers · Show" banner, new and edited comments are inserted at once,
scores and accepted marks change quietly.

The ETag has two parts, "<stamp>-<snapshot>":

- stamp: the time stamps the query cache keeps for posts and users (any
  save of a post, vote, accept, state change or user changes them), the
  viewer, the language, `since` and a STAMP_BUCKET_SECONDS time bucket
  (changes made with QuerySet.update() don't touch the stamps: the bucket
  bounds how long they go unnoticed). When the browser's stamp part is
  current the answer is 304 without any query of its own.
- snapshot: a hash of the snapshot (one query on forum_node). A stamp that
  changed because of something elsewhere on the site still gives a 304 when
  this question's snapshot is the same, with the new ETag.

Visibility is the question page's: no deleted posts, held posts only for
their author and moderators (forum/utils/review.py), comments only under
posts the visitor sees. Clicking Show asks the same URL with `posts=<ids>`,
which renders those posts with the page's templates (node/answer.html,
node/comment.html) for the visitor.

Edit conflicts
--------------
The edit pages send the revision number they started from (`base_revision`,
compose/revision.html). When the post has another active revision by the
time the form is saved, nothing is saved: the form comes back with the
visitor's text, who changed the post and a link to its revisions; saving
again replaces that version (the form then carries the new revision).
"""
import datetime
import hashlib
import json
import re
import time

from django.core.cache import cache
from django.db.models import Q
from django.utils.translation import get_language

from forum.utils import review

POLL_SECONDS = 20
STAMP_BUCKET_SECONDS = 120
MAX_POSTS = 50

# Search engines and link previews get the page without the live script.
CRAWLER_RE = re.compile(r'bot\b|bot/|crawl|spider|slurp|archiver|facebookexternalhit|embedly|preview|'
                        r'mediapartners|lighthouse|pingdom', re.IGNORECASE)


def is_crawler(request):
    return bool(CRAWLER_RE.search(request.META.get('HTTP_USER_AGENT', '')))


def now_token():
    """The `since` token for a page rendered now."""
    return str(int(time.time() * 1000))


def parse_token(value):
    """The datetime of a `since` token (naive, server time), or None."""
    if not value or not value.isdigit() or len(value) > 15:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(value) / 1000.0)
    except (OverflowError, OSError, ValueError):
        return None


# -- ETag ---------------------------------------------------------------------

def stamp(user, since):
    """The cheap part of the ETag, or None when the cache keeps no time stamps."""
    from forum.models import Node, User
    keys = [Node._get_cache_query_invalidation_key(), User._get_cache_query_invalidation_key()]
    stamps = cache.get_many(keys)
    if len(stamps) != len(keys):
        return None
    raw = '|'.join([str(user.id or 0), get_language() or '', since, str(int(time.time() // STAMP_BUCKET_SECONDS))] +
                   [stamps[key].isoformat() for key in keys])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def split_etag(header):
    """(stamp, snapshot hash) of the first ETag in an If-None-Match header."""
    match = re.match(r'\s*(?:W/)?"([0-9a-f]{1,32})-([0-9a-f]{1,32})"', header or '')
    return (match.group(1), match.group(2)) if match else (None, None)


def make_etag(stamp_part, snapshot_hash):
    return '"%s-%s"' % (stamp_part or '0', snapshot_hash)


# -- snapshot -----------------------------------------------------------------

def _visible_row(state, author_id, user, moderator):
    if review.DELETED_MARK in state:
        return False
    if review.HELD_MARK in state and not moderator and not (user.is_authenticated and author_id == user.id):
        return False
    return True


def snapshot(question, user, since):
    """What `user` sees of `question` now (see the module docstring); one query."""
    from forum.models import Node
    moderator = review.is_moderator(user)
    rows = Node.objects.filter(Q(id=question.id) | Q(abs_parent_id=question.id) | Q(parent_id=question.id)).values_list(
        'id', 'node_type', 'parent_id', 'author_id', 'score', 'marked', 'state_string', 'active_revision_id',
        'added_at').order_by('id')
    answers, comments, own = [], [], None
    shown = {question.id}
    pending_comments = []
    for node_id, node_type, parent_id, author_id, score, marked, state, rev, added_at in rows:
        state = state or ''
        if node_id == question.id:
            own = {'id': node_id, 'rev': rev, 'score': score}
        elif node_type == 'answer' and parent_id == question.id:
            if _visible_row(state, author_id, user, moderator):
                shown.add(node_id)
                answers.append({'id': node_id, 'rev': rev, 'score': score,
                                'accepted': bool(marked) or '(accepted)' in state,
                                'new': bool(since and added_at and added_at > since)})
        elif node_type == 'comment':
            if _visible_row(state, author_id, user, moderator):
                pending_comments.append({'id': node_id, 'parent': parent_id, 'rev': rev, 'score': score})
    comments = [c for c in pending_comments if c['parent'] in shown]
    return {'question': own, 'answers': answers, 'comments': comments}


def snapshot_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()[:16]


# -- edit conflicts -----------------------------------------------------------

def _posted_base(request):
    value = request.POST.get('base_revision', '')
    return int(value) if value.isdigit() else None


def edit_conflict(request, post, form):
    """When the edit form of `post` changes it but the post has another active
    revision than the one the form started from: that revision, else None."""
    from forum.models import Node, NodeRevision
    base = _posted_base(request)
    if base is None or not form.has_changed():
        return None
    current = Node.objects.filter(id=post.id).values_list('active_revision__revision', flat=True).first()
    if current is None or current == base:
        return None
    return NodeRevision.objects.filter(node_id=post.id, revision=current).select_related('author').first()


def edit_context(request, post, conflict):
    """`base_revision` (for the hidden field) and `edit_conflict` for the edit templates."""
    if conflict is not None:
        base = conflict.revision
    elif request.method == 'POST' and _posted_base(request) is not None:
        base = _posted_base(request)
    else:
        base = post.active_revision.revision if post.active_revision_id else ''
    return {'base_revision': base, 'edit_conflict': conflict}
