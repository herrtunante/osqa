from .base import *
from .node import Node
from .user import User


class ModerationReport(models.Model):
    """A post (node) or an account (user, e.g. profile spam) that an automated
    check such as the AI module thinks moderators should look at. Unlike a
    user flag it costs no reputation and never hides, suspends or deletes
    anything by itself; moderators resolve it from Admin > Flagged posts."""

    node = models.ForeignKey(Node, null=True, blank=True, related_name='moderation_reports', on_delete=models.CASCADE)
    user = models.ForeignKey(User, null=True, blank=True, related_name='moderation_reports', on_delete=models.CASCADE)
    source = models.CharField(max_length=32)
    verdict = models.CharField(max_length=32)
    confidence = models.FloatField(default=0)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=datetime.datetime.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    resolution = models.CharField(max_length=16, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('-created_at',)
        indexes = [models.Index(fields=['resolved_at', 'created_at'], name='modreport_open_idx')]

    @property
    def confidence_percent(self):
        return int(round(self.confidence * 100))

    @property
    def account_has_posts(self):
        return self.user_id is not None and Node.objects.filter(author_id=self.user_id).exists()

    @classmethod
    def open_reports(cls):
        return cls.objects.filter(resolved_at=None).select_related('node', 'node__author', 'user')

    def resolve(self, user, resolution):
        self.resolved_at = datetime.datetime.now()
        self.resolved_by = user
        self.resolution = resolution
        self.save()
