import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _

from .node import Node, NodeRevision
from .user import User


class SuggestedEdit(models.Model):
    """An edit to a question or answer proposed by a member who can't edit
    others' posts yet (below REP_TO_EDIT_OTHERS), waiting for a reviewer.
    The rules (who may suggest and review, conflicts, approving) are in
    forum/utils/suggested_edits.py; approving creates a normal revision
    credited to the suggester (SuggestedEditAction)."""

    PENDING, APPROVED, REJECTED, SUPERSEDED = 'pending', 'approved', 'rejected', 'superseded'
    STATUSES = (
        (PENDING, _('Waiting for review')),
        (APPROVED, _('Approved')),
        (REJECTED, _('Rejected')),
        (SUPERSEDED, _('Superseded')),
    )

    node = models.ForeignKey(Node, related_name='suggested_edits', on_delete=models.CASCADE)
    author = models.ForeignKey(User, related_name='suggested_edits', on_delete=models.CASCADE)
    # The proposal: title and tags for questions (empty for answers) and the Markdown body.
    title = models.CharField(max_length=300, blank=True)
    tagnames = models.CharField(max_length=125, blank=True)
    body = models.TextField()
    summary = models.CharField(max_length=300)
    # The revision the suggester started from (to find conflicting edits made since).
    base_revision = models.ForeignKey(NodeRevision, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING)
    reviewer = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    # A key of forum.utils.suggested_edits.REJECT_REASONS (rejected) or 'conflict' (superseded),
    # and the reviewer's own words.
    reason = models.CharField(max_length=32, blank=True)
    comment = models.CharField(max_length=300, blank=True)
    # The SuggestedEditAction (approved) or RejectSuggestedEditAction (rejected).
    action = models.ForeignKey('Action', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=datetime.datetime.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('created_at', 'id')
        indexes = [
            models.Index(fields=['status', 'created_at'], name='suggestededit_status_idx'),
            models.Index(fields=['node', 'status'], name='suggestededit_node_idx'),
            models.Index(fields=['author', 'created_at'], name='suggestededit_author_idx'),
        ]

    def __str__(self):
        return 'Suggested edit %s to %s (%s)' % (self.id, self.node_id, self.status)

    @property
    def post(self):
        return self.node.leaf

    @property
    def is_pending(self):
        return self.status == self.PENDING
