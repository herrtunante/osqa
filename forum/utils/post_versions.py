"""Version-aware questions and answers.

Moderators keep an ordered list of versions per tag (TagVersion, changed only
through forum/utils/tags.py) and mark one as current. A question or answer
may say which version of each versioned tag of its question it applies to
(NodeVersion, one row per post and tag). On the question page a post shows
a chip ("collect 4.1"); an answer written for an older version than the
current one shows a notice, and members who may edit it confirm "Still works
in 4.1" (ConfirmVersionAction, forum/actions/version.py: undoable) without
editing the text.

Storage: NodeVersion rows rather than Node.extra, so the search filter
(`version=`, forum/search/filters.py) matches them in SQL and merging tags
moves them with an UPDATE. The version lives on the post, not in its
revisions (like the community wiki flag): an edit that changes only the
version saves it without a new revision. Suggested edits don't carry a
version.

Queries: the versions of every tag are one catalogue in the shared cache
(catalog(): one query when the cache is cold, none after; emptied by every
change of versions or tag names, invalidate()). With POST_VERSIONS off, or
no versions at all, nothing else is queried. The question page loads the
versions of the question and all its answers with one query (for_thread()),
question lists one query per page (prefetch()), and the members who
confirmed come from the object cache.
"""
import re

from django.core.cache import cache
from django.db.models import Q

from forum import REQUEST_HOLDER, settings

CACHE_KEY = 'osqa:tag-versions:1'
NAME_RE = re.compile(r'^\w[\w.+-]*$', re.UNICODE)
MAX_NAME_LENGTH = 20
FIELD_PREFIX = 'version_'


def enabled():
    return bool(settings.POST_VERSIONS)


def field_name(tag_id):
    """The form field of a tag's "Applies to version" select."""
    return '%s%d' % (FIELD_PREFIX, tag_id)


class Version:
    """A TagVersion as kept in the catalogue (plain values, no queries)."""
    __slots__ = ('id', 'tag_id', 'tag_name', 'name', 'position', 'is_current', 'released')

    def __init__(self, id, tag_id, tag_name, name, position, is_current, released):
        self.id, self.tag_id, self.tag_name, self.name = id, tag_id, tag_name, name
        self.position, self.is_current, self.released = position, is_current, released

    @property
    def label(self):
        return '%s %s' % (self.tag_name, self.name)

    def __repr__(self):
        return '<Version %s>' % self.label


class Catalog:
    """Every tag's versions, oldest first, and the names (synonyms too) of
    the tags that have versions."""

    def __init__(self, rows=(), synonyms=()):
        self.by_id, self.by_tag, self.tag_names, self.current = {}, {}, {}, {}
        for row in rows:
            version = Version(*row)
            self.by_id[version.id] = version
            self.by_tag.setdefault(version.tag_id, []).append(version)
            self.tag_names[version.tag_id] = version.tag_name
            if version.is_current:
                self.current[version.tag_id] = version
        for versions in self.by_tag.values():
            versions.sort(key=lambda v: (v.position, v.id))
        self.names = dict((name, tag_id) for tag_id, name in self.tag_names.items())
        self.aliases = dict((tag_id, [name]) for tag_id, name in self.tag_names.items())
        for name, tag_id in synonyms:
            if tag_id in self.tag_names:
                self.names.setdefault(name, tag_id)
                self.aliases[tag_id].append(name)

    def __bool__(self):
        return bool(self.by_id)

    def versions(self, tag_id):
        return self.by_tag.get(tag_id, [])

    def tag_ids_for(self, names):
        """The ids of the tags among `names` that have versions, in order."""
        found = []
        for name in names or ():
            tag_id = self.names.get(name)
            if tag_id is not None and tag_id not in found:
                found.append(tag_id)
        return found

    def newer(self, a, b):
        """Whether version `a` comes after version `b` (same tag)."""
        return (a.position, a.id) > (b.position, b.id)


def _load():
    from forum.models import TagSynonym, TagVersion
    rows = list(TagVersion.objects.order_by('tag_id', 'position', 'id').values_list(
        'id', 'tag_id', 'tag__name', 'name', 'position', 'is_current', 'released'))
    synonyms = []
    if rows:
        synonyms = list(TagSynonym.objects.filter(target_id__in=set(r[1] for r in rows)).values_list(
            'source_name', 'target_id'))
    return rows, synonyms


def catalog():
    """The catalogue (kept for the request; from the shared cache)."""
    request = REQUEST_HOLDER.request
    memo = getattr(request, '_osqa_tag_versions', None) if request is not None else None
    if memo is not None:
        return memo
    data = cache.get(CACHE_KEY)
    if data is None:
        data = _load()
        cache.set(CACHE_KEY, data, None)
    found = Catalog(*data)
    if request is not None:
        request._osqa_tag_versions = found
    return found


def _forget_posts(tag_versions=False):
    """What was kept for the request, and the cache time stamps the anonymous
    page ETags are made of (forum/utils/conditional.py), so pages showing
    versions are not answered with an old copy."""
    from forum.models import Node, Tag
    request = REQUEST_HOLDER.request
    if request is not None:
        request.__dict__.pop('_osqa_post_versions', None)
        if tag_versions:
            request.__dict__.pop('_osqa_tag_versions', None)
    Node._set_query_cache_invalidation_timestamp()
    if tag_versions:
        Tag._set_query_cache_invalidation_timestamp()


def invalidate():
    """After any change of tag versions, of a versioned tag's name or synonyms."""
    cache.delete(CACHE_KEY)
    _forget_posts(tag_versions=True)


def active_catalog():
    """The catalogue, or None when the feature is off or there are no versions."""
    if not enabled():
        return None
    found = catalog()
    return found or None


# -- what a post applies to ----------------------------------------------------

class PostVersion:
    """The version of one tag a post applies to, as shown."""

    def __init__(self, node_id, version, current=None, confirmed=None, confirmed_by=None, confirmed_at=None,
                 confirmed_action_id=None):
        self.node_id, self.version, self.current = node_id, version, current
        self.confirmed, self.confirmed_by, self.confirmed_at = confirmed, confirmed_by, confirmed_at
        self.confirmed_action_id = confirmed_action_id

    @property
    def tag_name(self):
        return self.version.tag_name

    @property
    def tag_id(self):
        return self.version.tag_id

    @property
    def effective(self):
        """The newest version it is known to work in: its own, or a newer confirmed one."""
        if self.confirmed is not None and (self.confirmed.position, self.confirmed.id) > (
                self.version.position, self.version.id):
            return self.confirmed
        return self.version

    @property
    def outdated(self):
        """A newer version than the one it was written (or confirmed) for is current."""
        current, effective = self.current, self.effective
        return current is not None and (current.position, current.id) > (effective.position, effective.id)

    @property
    def confirmed_current(self):
        return self.confirmed is not None and self.current is not None and self.confirmed.id == self.current.id

    def data(self):
        """For the JSON API and the agents' Markdown."""
        from forum.api.data import user_brief
        from forum.api.core import iso
        return {
            'tag': self.tag_name,
            'version': self.version.name,
            'current_version': self.current.name if self.current else None,
            'outdated': self.outdated,
            'confirmed_for': {
                'version': self.confirmed.name,
                'by': user_brief(self.confirmed_by),
                'at': iso(self.confirmed_at),
            } if self.confirmed is not None else None,
        }


VALUES = ('node_id', 'tag_id', 'version_id', 'confirmed_version_id', 'confirmed_by_id', 'confirmed_at',
          'confirmed_action_id')


def _build(rows, cat):
    """{node id: [PostVersion]} from NodeVersion value rows; the members who
    confirmed from the object cache (one round trip)."""
    from forum.models import User
    from forum.models.base import load_cached
    users = load_cached(User, set(r[4] for r in rows if r[4]))
    found = {}
    for node_id, tag_id, version_id, confirmed_id, by_id, at, action_id in rows:
        version = cat.by_id.get(version_id)
        if version is None:
            continue
        found.setdefault(node_id, []).append(PostVersion(
            node_id, version, cat.current.get(tag_id), cat.by_id.get(confirmed_id), users.get(by_id), at, action_id))
    for versions in found.values():
        versions.sort(key=lambda pv: pv.tag_name)
    return found


def for_nodes(node_ids, tag_names=None):
    """{node id: [PostVersion]} for the posts (one query; none without versions).
    With `tag_names`, only the versions of those tags (the question's)."""
    cat = active_catalog()
    node_ids = [i for i in node_ids if i]
    if cat is None or not node_ids:
        return {}
    from forum.models import NodeVersion
    rows = NodeVersion.objects.filter(node_id__in=node_ids)
    if tag_names is not None:
        tag_ids = cat.tag_ids_for(tag_names)
        if not tag_ids:
            return {}
        rows = rows.filter(tag_id__in=tag_ids)
    return _build(list(rows.values_list(*VALUES)), cat)


def for_thread(question):
    """{node id: [PostVersion]} for a question and all its answers: one query,
    kept for the rest of the request (the question page renders each post)."""
    cat = active_catalog()
    if cat is None:
        return {}
    request = REQUEST_HOLDER.request
    memo = request.__dict__.setdefault('_osqa_post_versions', {}) if request is not None else {}
    if question.id not in memo:
        tag_ids = cat.tag_ids_for(question.tagname_list())
        if not tag_ids:
            memo[question.id] = {}
        else:
            from forum.models import NodeVersion
            rows = NodeVersion.objects.filter(Q(node_id=question.id) | Q(node__parent_id=question.id),
                                              tag_id__in=tag_ids).values_list(*VALUES)
            memo[question.id] = _build(list(rows), cat)
    return memo[question.id]


def of_post(post):
    """[PostVersion] of a question or answer shown on its question page."""
    question = post if post.node_type == 'question' else post.parent
    if question is None:
        return []
    return for_thread(question).get(post.id, [])


def prefetch(questions):
    """Gives each question of a list page its own versions (attribute
    `osqa_versions`) with one query; for prefetch_cached(after=...)."""
    questions = [q for q in questions if getattr(q, 'node_type', None) == 'question']
    cat = active_catalog()
    if cat is None or not questions:
        return
    wanted = [q.id for q in questions if cat.tag_ids_for(q.tagname_list())]
    found = {}
    if wanted:
        from forum.models import NodeVersion
        found = _build(list(NodeVersion.objects.filter(node_id__in=wanted).values_list(*VALUES)), cat)
    for question in questions:
        names = set(question.tagname_list())
        question.osqa_versions = [pv for pv in found.get(question.id, ()) if pv.tag_name in names]


# -- the "Applies to version" selects -------------------------------------------

class VersionGroups(list):
    """The selects of a form; `follow_tags`: shown as tags are typed, `any_shown`:
    one of them is shown at first."""
    follow_tags = False

    @property
    def any_shown(self):
        return any(not group['hidden'] for group in self)


def form_groups(tag_names, node=None, data=None, show_all=False):
    """The selects of a post form, one per versioned tag of `tag_names` (an
    answer: its question's tags). With `show_all` (ask and edit question,
    where tags are typed) every versioned tag gets one, hidden until its tag
    is in the tags field (media/js/osqa.versions.js). The choice comes from
    `data` (a form sent back), else from the post's saved versions."""
    groups = VersionGroups()
    groups.follow_tags = show_all
    cat = active_catalog()
    if cat is None:
        return groups
    shown = cat.tag_ids_for(tag_names)
    tag_ids = list(cat.by_tag) if show_all else shown
    if not tag_ids:
        return groups
    selected = {}
    if data is not None:
        selected = dict((tag_id, str(data.get(field_name(tag_id), '') or '')) for tag_id in tag_ids)
    elif node is not None and node.id:
        from forum.models import NodeVersion
        selected = dict((tag_id, str(version_id)) for tag_id, version_id in NodeVersion.objects.filter(
            node_id=node.id).values_list('tag_id', 'version_id'))
    for tag_id in sorted(tag_ids, key=lambda t: cat.tag_names[t]):
        groups.append({
            'tag': cat.tag_names[tag_id],
            'tag_id': tag_id,
            'field': field_name(tag_id),
            'names': ' '.join(cat.aliases[tag_id]),
            'hidden': tag_id not in shown,
            'options': [{'id': v.id, 'name': v.name, 'current': v.is_current,
                         'selected': str(v.id) == selected.get(tag_id, '')}
                        for v in reversed(cat.versions(tag_id))],
        })
    return groups


def choices(data, tag_names):
    """{tag id: version id or None} sent in `data` for the versioned tags of
    `tag_names`; tags whose select wasn't sent are left out (kept as they
    are), a value that isn't one of the tag's versions counts as none."""
    cat = active_catalog()
    if cat is None or data is None:
        return {}
    found = {}
    for tag_id in cat.tag_ids_for(tag_names):
        name = field_name(tag_id)
        if name not in data:
            continue
        raw = str(data.get(name) or '').strip()
        version = cat.by_id.get(int(raw)) if raw.isdigit() else None
        found[tag_id] = version.id if version is not None and version.tag_id == tag_id else None
    return found


def save(node, data, tag_names):
    """Stores the versions sent with a post form (see choices()). A question
    loses the versions of tags it no longer has. Changing the version drops
    a confirmation that isn't newer than the new version. Returns whether
    anything changed."""
    cat = active_catalog()
    if cat is None or node is None or not node.id:
        return False
    from forum.models import NodeVersion
    wanted = choices(data, tag_names)
    existing = dict((nv.tag_id, nv) for nv in NodeVersion.objects.filter(node_id=node.id))
    changed = False
    for tag_id, version_id in wanted.items():
        row = existing.get(tag_id)
        if version_id is None:
            if row is not None:
                row.delete()
                changed = True
        elif row is None:
            NodeVersion.objects.create(node_id=node.id, tag_id=tag_id, version_id=version_id)
            changed = True
        elif row.version_id != version_id:
            row.version_id = version_id
            confirmed = cat.by_id.get(row.confirmed_version_id)
            if confirmed is None or not cat.newer(confirmed, cat.by_id[version_id]):
                row.confirmed_version = row.confirmed_by = row.confirmed_at = row.confirmed_action = None
            row.save()
            changed = True
    if node.node_type == 'question':
        keep = set(cat.tag_ids_for(tag_names))
        for tag_id, row in existing.items():
            if tag_id not in keep and tag_id not in wanted:
                row.delete()
                changed = True
    if changed:
        _forget_posts()
    return changed


def question_tag_names(post):
    """The tags whose versions an answer or question may choose: the question's."""
    question = post if post.node_type == 'question' else post.parent
    return question.tagname_list() if question is not None else []


# -- "Still works in <current>" ---------------------------------------------------

def can_confirm(user, post, post_version):
    """Members who may edit the answer (its author, REP_TO_EDIT_OTHERS, ...)
    confirm an outdated answer for the current version."""
    return bool(enabled() and user is not None and user.is_authenticated and post.node_type == 'answer'
                and post_version is not None and post_version.outdated and not post.nis.deleted
                and user.can_edit_post(post))


def can_undo(user, post_version):
    """Whoever confirmed, or a moderator, takes a confirmation back."""
    return bool(post_version is not None and post_version.confirmed_action_id and user is not None
                and user.is_authenticated and (user.id == getattr(post_version.confirmed_by, 'id', None)
                                               or user.is_staff or user.is_superuser))


def confirm(user, answer, tag_id, ip=''):
    """Confirms the answer's version of `tag_id` for the tag's current version:
    the ConfirmVersionAction, or None when there is nothing to confirm."""
    from forum.actions import ConfirmVersionAction
    found = [pv for pv in for_nodes([answer.id]).get(answer.id, []) if pv.tag_id == tag_id]
    post_version = found[0] if found else None
    if not can_confirm(user, answer, post_version):
        return None
    return ConfirmVersionAction(user=user, node=answer, ip=ip).save(data=dict(
        tag_id=tag_id, version_id=post_version.current.id))


def apply_confirmation(node_id, tag_id, version_id, user_id, at, action_id):
    """Sets (or with version_id None clears) a post's confirmation."""
    from forum.models import NodeVersion
    NodeVersion.objects.filter(node_id=node_id, tag_id=tag_id).update(
        confirmed_version_id=version_id, confirmed_by_id=user_id, confirmed_at=at, confirmed_action_id=action_id)
    _forget_posts()
