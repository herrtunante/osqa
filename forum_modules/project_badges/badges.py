from django.utils.translation import gettext as _
from forum.badges.base import AbstractBadge
from forum.models import Badge, Tag
from forum.actions import VoteUpAction

from . import settings

class BugBuster(AbstractBadge):
    type = Badge.SILVER
    name = _("Bug Buster")
    listen_to = (VoteUpAction, )

    @property
    def description(self):
        return _('Got %s upvotes in a question tagged with "bug"') % str(settings.BUG_BUSTER_VOTES_UP)

    def award_to(self, action):
        if action.node.node_type == "question" and int(action.node.score) == int(settings.BUG_BUSTER_VOTES_UP):
            try:
                bug = Tag.objects.get(name="bug")
                if bug in action.node.tags.all():
                    return action.node.author
            except Tag.DoesNotExist:
                pass