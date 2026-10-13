from .base import *
from .node import Node
from .user import User


class ModerationReport(models.Model):
    """A post that an automated check (e.g. the AI moderation module) thinks
    moderators should look at. Unlike a user flag it costs the author no
    reputation and never hides or deletes the post by itself; moderators
    resolve it from Admin > Flagged posts."""

    node = models.ForeignKey(Node, related_name='moderation_reports', on_delete=models.CASCADE)
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

    @classmethod
    def open_reports(cls):
        return cls.objects.filter(resolved_at=None).select_related('node', 'node__author')

    def resolve(self, user, resolution):
        self.resolved_at = datetime.datetime.now()
        self.resolved_by = user
        self.resolution = resolution
        self.save()
