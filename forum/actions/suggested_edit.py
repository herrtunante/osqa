"""Reviewing suggested edits (forum/utils/suggested_edits.py does the checks).

SuggestedEditAction is the edit itself, credited to the suggester: its user
is the suggester (the revision's author, who gets the reputation), real_user
the reviewer who approved it. As a ReviseAction it notifies the people the
edit newly mentions. RejectSuggestedEditAction is the reviewer's. Both can be
cancelled (undo): the suggestion goes back to the queue."""
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from forum.models import NodeRevision

from .node import ReviseAction
from forum.models.action import ActionProxy

__all__ = ['SuggestedEditAction', 'RejectSuggestedEditAction']


def _suggestion_of(action):
    from forum.models import SuggestedEdit
    suggestion = getattr(action, '_suggestion', None)
    if suggestion is None:
        extra = action.extra if isinstance(action.extra, dict) else {}
        query = SuggestedEdit.objects.filter(id=extra['suggestion']) if extra.get('suggestion') else \
            SuggestedEdit.objects.filter(action_id=action.id)
        suggestion = query.first()
    return suggestion


class SuggestedEditAction(ReviseAction):
    verb = _lazy("edited")

    def repute_users(self):
        from forum.utils.suggested_edits import rep_gain_for
        gain = rep_gain_for(self.user)
        if gain:
            self.repute(self.user, gain)

    def process_action(self):
        import datetime
        from forum.models import SuggestedEdit
        super().process_action()
        suggestion = _suggestion_of(self)
        if suggestion is not None:
            suggestion.status = SuggestedEdit.APPROVED
            suggestion.reviewer = self.real_user
            suggestion.reviewed_at = datetime.datetime.now()
            suggestion.action = self
            suggestion.save()

    def cancel_action(self):
        from forum.utils.suggested_edits import reopen
        suggestion = _suggestion_of(self)
        node = self.node.leaf
        try:
            number = int(self.extra)
        except (TypeError, ValueError):
            number = None
        if number and node.active_revision and node.active_revision.revision == number:
            previous = NodeRevision.objects.filter(node=node, revision__lt=number).order_by('-revision').first()
            if previous is not None:
                node.activate_revision(self.canceled_by or self.user, previous)
        if suggestion is not None:
            reopen(suggestion)

    @property
    def reviewer(self):
        return self.real_user

    def describe(self, viewer=None):
        reviewer = self.real_user
        if reviewer is None:
            return super().describe(viewer)
        return _("%(user)s edited %(post_desc)s (suggested edit approved by %(reviewer)s)") % {
            'user': self.hyperlink(self.user.get_profile_url(), self.friendly_username(viewer, self.user)),
            'post_desc': self.describe_node(viewer, self.node),
            'reviewer': self.hyperlink(reviewer.get_profile_url(), self.friendly_username(viewer, reviewer)),
        }


class RejectSuggestedEditAction(ActionProxy):
    """`extra`: {'suggestion': id, 'reason': key, 'comment': the reviewer's words}."""
    verb = _lazy("rejected a suggested edit")

    def process_action(self):
        import datetime
        from forum.models import SuggestedEdit
        suggestion = _suggestion_of(self)
        if suggestion is not None:
            extra = self.extra or {}
            suggestion.status = SuggestedEdit.REJECTED
            suggestion.reviewer = self.user
            suggestion.reason = extra.get('reason', '')
            suggestion.comment = extra.get('comment', '')
            suggestion.reviewed_at = datetime.datetime.now()
            suggestion.action = self
            suggestion.save()

    def cancel_action(self):
        from forum.utils.suggested_edits import reopen
        suggestion = _suggestion_of(self)
        if suggestion is not None:
            reopen(suggestion)

    def describe(self, viewer=None):
        from forum.utils.suggested_edits import reason_label
        suggestion = _suggestion_of(self)
        author = suggestion.author if suggestion is not None else None
        return _("%(user)s rejected an edit suggested by %(author)s to %(post_desc)s: %(reason)s") % {
            'user': self.hyperlink(self.user.get_profile_url(), self.friendly_username(viewer, self.user)),
            'author': self.hyperlink(author.get_profile_url(), self.friendly_username(viewer, author)) if author
            else escape(_('someone')),
            'post_desc': self.describe_node(viewer, self.node),
            'reason': escape(reason_label((self.extra or {}).get('reason'))),
        }
