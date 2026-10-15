from .base import *
from .node import Node
from .user import User


class KnowledgeChunk(models.Model):
    """A piece of text an assistant can draw on: a forum thread (source
    'forum') or part of a crawled documentation page ('web'), with its
    embedding vector (float32 bytes) for semantic search."""

    source = models.CharField(max_length=16)
    url = models.CharField(max_length=500)
    position = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=300, blank=True)
    text = models.TextField()
    node = models.ForeignKey(Node, null=True, blank=True, related_name='knowledge_chunks', on_delete=models.CASCADE)
    quality = models.FloatField(default=1.0)
    content_hash = models.CharField(max_length=40)
    embedding = models.BinaryField(null=True, blank=True)
    updated_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        unique_together = (('source', 'url', 'position'),)


class AISuggestion(models.Model):
    """An AI-written suggested answer to an unanswered question. Shown,
    clearly marked, below the question; it is not an Answer: it earns no
    reputation, doesn't count as an answer and is never fed back into the
    knowledge index."""

    SHOWN, HELPFUL, REMOVED, DECLINED = 'shown', 'helpful', 'removed', 'declined'

    question = models.ForeignKey(Node, related_name='ai_suggestions', on_delete=models.CASCADE)
    body = models.TextField(blank=True)
    html = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, default=SHOWN)
    model = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=datetime.datetime.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)

    class Meta:
        app_label = 'forum'
        ordering = ('-created_at',)
        # Every question page looks up its suggestion.
        indexes = [models.Index(fields=['question', 'status', 'created_at'], name='aisuggestion_question_idx')]

    @property
    def visible(self):
        return self.status in (self.SHOWN, self.HELPFUL)

    def resolve(self, user, status):
        self.status = status
        self.resolved_by = user
        self.resolved_at = datetime.datetime.now()
        self.save()
