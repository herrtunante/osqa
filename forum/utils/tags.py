"""Tag management for moderators (forum/views/tags.py): rename a tag, merge
one tag into another, add and remove synonyms, edit the description.

Every change is recorded as an Action (forum/actions/tag.py) and listed on
the tag's management page; renames, synonyms and descriptions can be undone
there, a merge cannot (its confirmation page says how many questions move).

What a change keeps right:
- Questions: the current tags change through a new revision per question
  (summary "Tag x merged into y"), so earlier revisions keep the names they
  had; the question's last activity is left alone. Deleted questions and
  posts held for review are retagged too, so they land in the right tag if
  they come back.
- Counts: Tag.used_count is counted again from the questions afterwards
  (not deleted, not held for review), instead of being adjusted.
- Search: each retagged question is saved, which updates its SearchDocument.
- Followers, interesting and ignored marks (MarkedTag) move to the target;
  someone who had marked both keeps their mark on the target.
- Synonyms: a merged tag's name becomes a synonym of the target (old links
  /tags/<old>/ redirect, typing the old name gives the target), its own
  synonyms point to the target, and the merged tag is deleted.
- Versions (TagVersion, forum/utils/post_versions.py): a rename keeps them
  (same tag row); a merge moves the merged tag's versions to the target
  after its own (a version of the same name becomes the target's, the
  target's current version stays current) and its posts' versions with them.

The tag's version list (add, remove, reorder, current) is kept here too and
recorded as TagVersionAction (history only: the controls reverse a change).
"""
import re

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from forum import settings

TAG_NAME_RE = re.compile(r'^[\w+#.-]+$', re.UNICODE)

# Action types written by forum/actions/tag.py (Action.get_type()).
TAG_ACTION_TYPES = ('renametag', 'mergetag', 'addtagsynonym', 'removetagsynonym', 'edittag', 'tagversion')

# Node.tagnames / NodeRevision.tagnames
MAX_TAGNAMES_LENGTH = 125


class TagError(Exception):
    """A change that can't be made; the message is shown to the moderator."""


class NeedsMerge(TagError):
    """The synonym is an existing tag: its questions have to be merged first."""

    def __init__(self, source, target):
        super().__init__(source.name)
        self.source, self.target = source, target


def can_manage_tags(user):
    """Moderators (staff) and superusers manage tags."""
    return bool(user is not None and user.is_authenticated and (user.is_superuser or user.is_staff))


def normalize(name):
    return (name or '').strip().lower()


def validate_name(name):
    if (not name or not TAG_NAME_RE.match(name) or len(name) > int(settings.FORM_MAX_LENGTH_OF_TAG)
            or len(name) < int(settings.FORM_MIN_LENGTH_OF_TAG)):
        raise TagError(_("Enter a valid tag name."))


def _unique(names):
    out = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


def tagged_questions(tag_id, name):
    """Every question tagged `name` (tag `tag_id`), deleted and held ones included."""
    from forum.models import Question
    candidates = Question.objects.filter(Q(tags__id=tag_id) | Q(tagnames__icontains=name)).distinct().order_by('id')
    return [question for question in candidates if name in question.tagname_list()]


def recount(tag):
    """Counts the tag's questions again (those shown: not deleted, not held for review)."""
    from forum.models import Question, Tag
    from forum.utils.review import hidden_q
    count = Question.objects.filter(tags__id=tag.id).exclude(hidden_q()).aggregate(
        n=Count('id', distinct=True))['n'] or 0
    Tag.objects.filter(id=tag.id).update(used_count=count)
    tag.used_count = count
    tag.uncache()
    Tag._set_query_cache_invalidation_timestamp()
    return count


def _retag(question, names, user, summary):
    """New revision of `question` with the tags `names`; its activity date stays."""
    from forum.models import Question
    last_at, last_by = question.last_activity_at, question.last_activity_by_id
    active = question.active_revision
    tagnames = ' '.join(names)
    if active is not None:
        question.create_revision(user, title=active.title, body=active.body, tagnames=tagnames, summary=summary)
    else:
        question.tagnames = tagnames
        question.save()
    # Housekeeping: the question doesn't move up the "active" list.
    Question.objects.filter(id=question.id).update(last_activity_at=last_at, last_activity_by=last_by)
    question.uncache()


def _replace(questions, old, new, user, summary):
    for question in questions:
        _retag(question, _unique([new if name == old else name for name in question.tagname_list()]), user, summary)
    from forum.models import Question
    Question._set_query_cache_invalidation_timestamp()


def _check_lengths(questions, old, new):
    for question in questions:
        names = _unique([new if name == old else name for name in question.tagname_list()])
        if len(' '.join(names)) > MAX_TAGNAMES_LENGTH:
            raise TagError(_("The tags of “%(title)s” would be too long with this name.") % {'title': question.title})


def _rename(tag, old, new, user, summary):
    """Renames `tag` in place (same row: followers and synonyms stay with it)
    and its questions' current tags."""
    questions = tagged_questions(tag.id, old)
    _check_lengths(questions, old, new)
    tag.uncache()  # cached under its old name
    tag.name = new
    tag.save()
    _replace(questions, old, new, user, summary)
    recount(tag)
    _versions_changed()
    return questions


# -- rename ------------------------------------------------------------------

def check_rename(tag, new_name):
    """The normalised new name, or TagError."""
    from forum.models import Tag, TagSynonym
    new_name = normalize(new_name)
    validate_name(new_name)
    if new_name == tag.name:
        raise TagError(_("The tag is already called %s.") % new_name)
    if Tag.objects.filter(name=new_name).exists():
        raise TagError(_("There is already a tag named %s: merge this tag into it instead.") % new_name)
    synonym = TagSynonym.objects.filter(source_name=new_name).select_related('target').first()
    if synonym and synonym.target_id != tag.id:
        raise TagError(_("%(name)s is a synonym of %(tag)s.") % {'name': new_name, 'tag': synonym.target.name})
    return new_name


def rename_tag(tag, new_name, user, ip, keep_synonym=True):
    """Renames `tag`; with `keep_synonym` its old name becomes a synonym, so
    old links and the old name typed in the tags field keep working. A
    synonym of this tag can become its name (the synonym goes)."""
    from forum.actions import RenameTagAction
    from forum.models import TagSynonym
    new_name = check_rename(tag, new_name)
    old = tag.name
    with transaction.atomic():
        replaced = bool(TagSynonym.objects.filter(source_name=new_name, target=tag).delete()[0])
        questions = _rename(tag, old, new_name, user, _("Tag %(old)s renamed to %(new)s") % {'old': old, 'new': new_name})
        synonym = keep_synonym and not TagSynonym.objects.filter(source_name=old).exists()
        if synonym:
            TagSynonym(source_name=old, target=tag, created_by=user).save()
        action = RenameTagAction(user=user, ip=ip, extra=dict(
            tag_id=tag.id, old=old, new=new_name, synonym=synonym, replaced_synonym=replaced,
            questions=len(questions)))
        action.save()
    return action


def can_undo_rename(extra):
    from forum.models import Tag, TagSynonym
    tag = Tag.objects.filter(id=extra['tag_id']).first()
    if tag is None or tag.name != extra['new'] or Tag.objects.filter(name=extra['old']).exists():
        return False
    synonym = TagSynonym.objects.filter(source_name=extra['old']).first()
    return synonym is None or (extra.get('synonym') and synonym.target_id == tag.id)


def undo_rename(extra, user):
    from forum.models import Tag, TagSynonym
    tag = Tag.objects.get(id=extra['tag_id'])
    old, new = extra['old'], extra['new']
    with transaction.atomic():
        if extra.get('synonym'):
            TagSynonym.objects.filter(source_name=old, target=tag).delete()
        _rename(tag, new, old, user, _("Tag %(old)s renamed to %(new)s") % {'old': new, 'new': old})
        if extra.get('replaced_synonym') and not TagSynonym.objects.filter(source_name=new).exists():
            TagSynonym(source_name=new, target=tag, created_by=user).save()


# -- merge -------------------------------------------------------------------

def merge_target(source, name):
    """The tag `source` would be merged into when `name` is typed (a synonym
    gives its target), or TagError."""
    from forum.models import Tag, TagSynonym
    name = normalize(name)
    if not name:
        raise TagError(_("Enter the name of the tag to merge into."))
    name = TagSynonym.resolve([name])[0]
    target = Tag.objects.filter(name=name).first()
    if target is None:
        raise TagError(_("There is no tag named %s.") % name)
    if target.id == source.id:
        raise TagError(_("A tag cannot be merged into itself."))
    return target


def merge_summary(source, target):
    """What merging `source` into `target` changes, for the confirmation page."""
    from forum.models import MarkedTag
    questions = tagged_questions(source.id, source.name)
    marks = MarkedTag.objects.filter(tag=source)
    target_users = MarkedTag.objects.filter(tag=target).values('user_id')
    return {
        'questions': len(questions),
        'both': sum(1 for q in questions if target.name in q.tagname_list()),
        'hidden': sum(1 for q in questions if not q.counts_in_tags),
        'followers': marks.filter(reason='good').count(),
        'ignoring': marks.filter(reason='bad').count(),
        'duplicate_marks': marks.filter(user_id__in=target_users).count(),
        'synonyms': list(source.synonyms.values_list('source_name', flat=True)),
        'versions': source.versions.count(),
    }


def merge_tags(source, target, user, ip):
    """Moves everything of `source` to `target`, makes `source`'s name a
    synonym of `target` and deletes `source`. Can't be undone."""
    from forum.actions import MergeTagAction
    from forum.models import MarkedTag, TagSynonym
    if source.id == target.id:
        raise TagError(_("A tag cannot be merged into itself."))
    with transaction.atomic():
        summary = merge_summary(source, target)
        questions = tagged_questions(source.id, source.name)
        _replace(questions, source.name, target.name, user,
                 _("Tag %(old)s merged into %(new)s") % {'old': source.name, 'new': target.name})

        # Marks: whoever marked both keeps their choice for the target.
        target_users = set(MarkedTag.objects.filter(tag=target).values_list('user_id', flat=True))
        MarkedTag.objects.filter(tag=source, user_id__in=target_users).delete()
        MarkedTag.objects.filter(tag=source).update(tag=target)

        TagSynonym.objects.filter(target=source).update(target=target)
        synonym = TagSynonym.objects.filter(source_name=source.name).first()
        if synonym is None:
            TagSynonym(source_name=source.name, target=target, created_by=user).save()
        elif synonym.target_id != target.id:
            synonym.target = target
            synonym.save()

        _merge_versions(source, target)

        source_name = source.name
        source.delete()  # nothing refers to it any more
        recount(target)
        _versions_changed()
        action = MergeTagAction(user=user, ip=ip, extra=dict(
            tag_id=target.id, source=source_name, target=target.name, **summary))
        action.save()
    return action


# -- synonyms ----------------------------------------------------------------

def check_synonym(tag, name):
    """The normalised synonym name, TagError, or NeedsMerge when `name` is an existing tag."""
    from forum.models import Tag, TagSynonym
    name = normalize(name)
    validate_name(name)
    if name == tag.name:
        raise TagError(_("A tag cannot be a synonym of itself."))
    if TagSynonym.objects.filter(source_name=name).exists():
        raise TagError(_("%s is already a synonym.") % name)
    if TagSynonym.objects.filter(source_name=tag.name).exists():
        raise TagError(_("%s is itself a synonym of another tag.") % tag.name)
    existing = Tag.objects.filter(name=name).first()
    if existing is not None:
        raise NeedsMerge(existing, tag)
    return name


def add_synonym(tag, name, user, ip):
    from forum.actions import AddTagSynonymAction
    from forum.models import TagSynonym
    name = check_synonym(tag, name)
    with transaction.atomic():
        TagSynonym(source_name=name, target=tag, created_by=user).save()
        _versions_changed()
        action = AddTagSynonymAction(user=user, ip=ip, extra=dict(tag_id=tag.id, tag=tag.name, synonym=name))
        action.save()
    return action


def remove_synonym(synonym, user, ip):
    from forum.actions import RemoveTagSynonymAction
    tag = synonym.target
    with transaction.atomic():
        synonym.delete()
        _versions_changed()
        action = RemoveTagSynonymAction(user=user, ip=ip, extra=dict(
            tag_id=tag.id, tag=tag.name, synonym=synonym.source_name))
        action.save()
    return action


def synonym_name_free(name):
    from forum.models import Tag, TagSynonym
    return not (TagSynonym.objects.filter(source_name=name).exists() or Tag.objects.filter(name=name).exists())


# -- description -------------------------------------------------------------

def edit_description(tag, text, user, ip):
    from forum.actions import EditTagAction
    text = (text or '').strip()[:2000]
    old = tag.description
    if text == old:
        return None
    with transaction.atomic():
        tag.description = text
        tag.save()
        action = EditTagAction(user=user, ip=ip, extra=dict(tag_id=tag.id, tag=tag.name, old=old, new=text))
        action.save()
    return action


# -- versions ----------------------------------------------------------------

def _versions_changed():
    from forum.utils import post_versions
    post_versions.invalidate()


def _merge_versions(source, target):
    """Moves the versions of `source` (and the posts' choices of them) to `target`."""
    from forum.models import NodeVersion, TagVersion
    own = dict((v.name, v) for v in TagVersion.objects.filter(tag=target))
    position = max([v.position for v in own.values()] or [-1]) + 1
    has_current = any(v.is_current for v in own.values())
    # A post tagged with both keeps its version of the target.
    NodeVersion.objects.filter(tag=source, node_id__in=NodeVersion.objects.filter(tag=target).values('node_id')).delete()
    for version in TagVersion.objects.filter(tag=source).order_by('position', 'id'):
        same = own.get(version.name)
        if same is not None:
            NodeVersion.objects.filter(version=version).update(version=same, tag=target)
            NodeVersion.objects.filter(confirmed_version=version).update(confirmed_version=same)
            version.delete()
            continue
        version.tag, version.position = target, position
        position += 1
        if version.is_current:
            version.is_current = not has_current
            has_current = True
        version.save()
    NodeVersion.objects.filter(tag=source).update(tag=target)


def validate_version_name(name):
    """The version label without surrounding spaces, or TagError."""
    from forum.utils.post_versions import MAX_NAME_LENGTH, NAME_RE
    name = (name or '').strip()
    if not name or len(name) > MAX_NAME_LENGTH or not NAME_RE.match(name):
        raise TagError(_("Enter a version of at most %d letters, digits, dots or dashes, e.g. 4.1.") % MAX_NAME_LENGTH)
    return name


def _record_version(tag, user, ip, change, version, **more):
    from forum.actions import TagVersionAction
    action = TagVersionAction(user=user, ip=ip, extra=dict(tag_id=tag.id, tag=tag.name, change=change,
                                                           version=version, **more))
    action.save()
    _versions_changed()
    return action


def _renumber(tag):
    """The tag's versions in order, numbered 0, 1, 2..."""
    from forum.models import TagVersion
    versions = list(TagVersion.objects.filter(tag=tag).order_by('position', 'id'))
    for index, version in enumerate(versions):
        if version.position != index:
            version.position = index
            version.save(update_fields=['position'])
    return versions


def add_version(tag, name, user, ip, current=False, released=None):
    """Adds a version after the tag's others (optionally the current one)."""
    from forum.models import TagVersion
    name = validate_version_name(name)
    if TagVersion.objects.filter(tag=tag, name__iexact=name).exists():
        raise TagError(_("%(tag)s already has a version %(version)s.") % {'tag': tag.name, 'version': name})
    with transaction.atomic():
        position = (TagVersion.objects.filter(tag=tag).aggregate(m=Max('position'))['m'] or 0) + 1
        if current:
            TagVersion.objects.filter(tag=tag, is_current=True).update(is_current=False)
        version = TagVersion.objects.create(tag=tag, name=name, position=position, is_current=bool(current),
                                            released=released)
        _renumber(tag)
        _record_version(tag, user, ip, 'added', name, current=bool(current))
    return version


def set_current_version(version, user, ip, current=True):
    """Makes `version` the tag's current version (or, with current=False, no version is current)."""
    from forum.models import TagVersion
    tag = version.tag
    with transaction.atomic():
        TagVersion.objects.filter(tag=tag, is_current=True).update(is_current=False)
        if current:
            TagVersion.objects.filter(id=version.id).update(is_current=True)
        _record_version(tag, user, ip, 'current' if current else 'not_current', version.name)


def move_version(version, offset, user, ip):
    """Moves `version` one place earlier (offset -1) or later (+1)."""
    tag = version.tag
    with transaction.atomic():
        versions = _renumber(tag)
        index = next(i for i, v in enumerate(versions) if v.id == version.id)
        other = index + (1 if offset > 0 else -1)
        if not 0 <= other < len(versions):
            return None
        a, b = versions[index], versions[other]
        a.position, b.position = b.position, a.position
        a.save(update_fields=['position'])
        b.save(update_fields=['position'])
        return _record_version(tag, user, ip, 'moved', version.name, after=b.name if offset > 0 else None,
                               before=b.name if offset < 0 else None)


def remove_version(version, user, ip):
    """Removes a version no post applies to (a typo, an announced version that never came)."""
    from forum.models import NodeVersion
    used = NodeVersion.objects.filter(version=version).count()
    if used:
        raise TagError(ngettext("%(count)d post applies to %(version)s, so it can't be removed.",
                                "%(count)d posts apply to %(version)s, so it can't be removed.", used) % {
            'count': used, 'version': version.name})
    tag = version.tag
    with transaction.atomic():
        name = version.name
        version.delete()
        _renumber(tag)
        _record_version(tag, user, ip, 'removed', name)


# -- history -----------------------------------------------------------------

def history(tag, limit=20, scan=500):
    """The latest recorded changes of `tag` (their leaf Action classes)."""
    from forum.models import Action
    found = []
    for action in Action.objects.filter(action_type__in=TAG_ACTION_TYPES).select_related('user').order_by(
            '-action_date', '-id')[:scan]:
        if isinstance(action.extra, dict) and action.extra.get('tag_id') == tag.id:
            found.append(action.leaf)
            if len(found) >= limit:
                break
    return found
