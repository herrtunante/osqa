""""Still works in <current version>" on an answer written for an older
version (forum/utils/post_versions.py). The answer's text is not edited;
`extra` keeps the tag, the confirmed version and the confirmation it
replaced, which cancelling puts back."""
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from forum.models.action import ActionProxy


class ConfirmVersionAction(ActionProxy):
    verb = _lazy("confirmed an answer for a new version")

    def process_data(self, tag_id=None, version_id=None):
        from forum.models import NodeVersion
        row = NodeVersion.objects.get(node_id=self.node.id, tag_id=tag_id)
        self.extra = {
            'tag_id': tag_id, 'version_id': version_id,
            'previous': [row.confirmed_version_id, row.confirmed_by_id,
                         row.confirmed_at.isoformat() if row.confirmed_at else None, row.confirmed_action_id],
        }

    def process_action(self):
        from forum.utils import post_versions
        post_versions.apply_confirmation(self.node.id, self.extra['tag_id'], self.extra['version_id'], self.user.id,
                                         self.action_date, self.id)

    def cancel_action(self):
        import datetime
        from forum.models import Action, NodeVersion, TagVersion
        from forum.utils import post_versions
        extra = self.extra if isinstance(self.extra, dict) else {}
        # Only while this is still the post's confirmation (not replaced by a later one).
        if not NodeVersion.objects.filter(node_id=self.node_id, tag_id=extra.get('tag_id'),
                                          confirmed_action_id=self.id).exists():
            return
        version_id, user_id, at, action_id = (extra.get('previous') or [None] * 4)
        # The earlier confirmation comes back unless its version was removed since.
        if version_id is not None and not TagVersion.objects.filter(id=version_id).exists():
            version_id = None
        if action_id is not None and not Action.objects.filter(id=action_id, canceled=False).exists():
            action_id = None
        post_versions.apply_confirmation(self.node_id, extra.get('tag_id'), version_id, user_id if version_id else None,
                                         datetime.datetime.fromisoformat(at) if (version_id and at) else None,
                                         action_id if version_id else None)

    def version_label(self):
        from forum.models import TagVersion
        version = TagVersion.objects.filter(id=(self.extra or {}).get('version_id')).select_related('tag').first()
        return version and '%s %s' % (version.tag.name, version.name) or ''

    def describe(self, viewer=None):
        return _("%(user)s confirmed that %(post_desc)s still works in %(version)s") % {
            'user': self.hyperlink(self.user.get_profile_url(), self.friendly_username(viewer, self.user)),
            'post_desc': self.describe_node(viewer, self.node),
            'version': escape(self.version_label()),
        }
