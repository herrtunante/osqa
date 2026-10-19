"""Moderators' changes to tags (forum/utils/tags.py does the work). They
have no node: `extra` names the tag (`tag_id`, the current tag after the
change) and what changed. Renames, synonyms and descriptions can be undone
from the tag's management page; a merge cannot."""
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from forum.models.action import ActionProxy


class TagActionMixin:
    undoable = True

    def tag_link(self, name):
        return self.hyperlink(reverse('tag_questions', kwargs={'tag': name}), name)

    def user_link(self, viewer):
        return self.hyperlink(self.user.get_profile_url(), self.friendly_username(viewer, self.user))

    def current_tag(self):
        from forum.models import Tag
        return Tag.objects.filter(id=(self.extra or {}).get('tag_id')).first()

    def get_absolute_url(self):
        tag = self.current_tag()
        return tag.get_absolute_url() if tag else reverse('tags')

    def can_undo(self):
        return False


class RenameTagAction(TagActionMixin, ActionProxy):
    verb = _lazy("renamed a tag")

    def describe(self, viewer=None):
        return _("%(user)s renamed the tag %(old)s to %(new)s") % {
            'user': self.user_link(viewer), 'old': '<span class="tag">%s</span>' % escape(self.extra['old']),
            'new': self.tag_link(self.extra['new'])}

    def can_undo(self):
        from forum.utils.tags import can_undo_rename
        return not self.canceled and can_undo_rename(self.extra)

    def cancel_action(self):
        from forum.utils.tags import undo_rename
        undo_rename(self.extra, self.canceled_by or self.user)


class MergeTagAction(TagActionMixin, ActionProxy):
    verb = _lazy("merged tags")
    undoable = False

    def describe(self, viewer=None):
        return _("%(user)s merged the tag %(source)s into %(target)s") % {
            'user': self.user_link(viewer), 'source': '<span class="tag">%s</span>' % escape(self.extra['source']),
            'target': self.tag_link(self.extra['target'])}


class AddTagSynonymAction(TagActionMixin, ActionProxy):
    verb = _lazy("added a tag synonym")

    def describe(self, viewer=None):
        return _("%(user)s made %(synonym)s a synonym of the tag %(tag)s") % {
            'user': self.user_link(viewer), 'synonym': '<span class="tag">%s</span>' % escape(self.extra['synonym']),
            'tag': self.tag_link(self.extra['tag'])}

    def can_undo(self):
        from forum.models import TagSynonym
        return not self.canceled and TagSynonym.objects.filter(
            source_name=self.extra['synonym'], target_id=self.extra['tag_id']).exists()

    def cancel_action(self):
        from forum.models import TagSynonym
        TagSynonym.objects.filter(source_name=self.extra['synonym'], target_id=self.extra['tag_id']).delete()


class RemoveTagSynonymAction(TagActionMixin, ActionProxy):
    verb = _lazy("removed a tag synonym")

    def describe(self, viewer=None):
        return _("%(user)s removed the synonym %(synonym)s of the tag %(tag)s") % {
            'user': self.user_link(viewer), 'synonym': '<span class="tag">%s</span>' % escape(self.extra['synonym']),
            'tag': self.tag_link(self.extra['tag'])}

    def can_undo(self):
        from forum.utils.tags import synonym_name_free
        return not self.canceled and self.current_tag() is not None and synonym_name_free(self.extra['synonym'])

    def cancel_action(self):
        from forum.models import TagSynonym
        tag = self.current_tag()
        if tag is not None:
            TagSynonym(source_name=self.extra['synonym'], target=tag, created_by=self.canceled_by or self.user).save()


class EditTagAction(TagActionMixin, ActionProxy):
    verb = _lazy("edited a tag description")

    def describe(self, viewer=None):
        return _("%(user)s edited the description of the tag %(tag)s") % {
            'user': self.user_link(viewer), 'tag': self.tag_link(self.extra['tag'])}

    def can_undo(self):
        tag = self.current_tag()
        return not self.canceled and tag is not None and tag.description == self.extra['new']

    def cancel_action(self):
        tag = self.current_tag()
        if tag is not None:
            tag.description = self.extra['old']
            tag.save()


class TagVersionAction(TagActionMixin, ActionProxy):
    """A change to the tag's version list (forum/utils/tags.py): `extra`
    has `change` (added, removed, current, not_current, moved) and the
    version's name. History only: the version controls reverse a change."""
    verb = _lazy("changed the versions of a tag")

    def describe(self, viewer=None):
        extra = self.extra or {}
        texts = {
            'added': _("%(user)s added the version %(version)s to the tag %(tag)s"),
            'removed': _("%(user)s removed the version %(version)s of the tag %(tag)s"),
            'current': _("%(user)s made %(version)s the current version of the tag %(tag)s"),
            'not_current': _("%(user)s marked no version of the tag %(tag)s as current"),
            'moved': _("%(user)s moved the version %(version)s of the tag %(tag)s"),
        }
        return texts.get(extra.get('change'), texts['moved']) % {
            'user': self.user_link(viewer), 'version': '<span class="version-chip">%s</span>' % escape(
                extra.get('version', '')), 'tag': self.tag_link(extra.get('tag', ''))}
