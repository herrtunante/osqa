from .base import *
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

class QuestionManager(NodeManager):
    def search(self, keywords, **kwargs):
        return False, self.filter(models.Q(title__icontains=keywords) | models.Q(body__icontains=keywords) | models.Q(children__body__icontains=keywords))

class Question(Node):
    class Meta(Node.Meta):
        proxy = True

    answer_count = DenormalizedField("children", ~models.Q(state_string__contains="(deleted)"), node_type="answer")
    accepted_count = DenormalizedField("children", ~models.Q(state_string__contains="(deleted)"), node_type="answer", marked=True)
    favorite_count = DenormalizedField("actions", action_type="favorite", canceled=False)

    friendly_name = _lazy("question")
    objects = QuestionManager()

    @property
    def closed(self):
        return self.nis.closed

    @property
    def view_count(self):
        return self.extra_count

    @property
    def headline(self):
        return self._headline()

    def _headline(self):
        if self.nis.deleted:
            return _('[deleted] ') + self.title

        if self.nis.closed:
            if self.duplicate_of_id:
                return self.title + _(' [duplicate]')
            return _('[closed] ') + self.title

        return self.title

    @property
    def duplicate_of_id(self):
        """Id of the question this one was closed as a duplicate of (kept in Node.extra)."""
        if self.nis.closed and isinstance(self.extra, dict):
            return self.extra.get('duplicate_of')
        return None

    @property
    def duplicate_of(self):
        if not self.duplicate_of_id:
            return None
        try:
            return Question.objects.filter_state(deleted=False).get(id=self.duplicate_of_id)
        except Question.DoesNotExist:
            return None

    @property
    def accepted_answers(self):
        return self.answers.filter(~models.Q(state_string__contains="(deleted)"), marked=True)
    def get_absolute_url(self):
        return reverse('question', kwargs={'id': self.id, 'slug': django_urlquote(slugify(self.title))})

    def meta_description(self):
        return self.summary

    def get_related_questions(self, count=10):
        cache_key = '%s.related_questions:%d:%d' % (settings.APP_URL, count, self.id)
        related_list = cache.get(cache_key)

        if related_list is None:
            related_list = Question.objects.filter_state(deleted=False).values('id').filter(tags__id__in=[t.id for t in self.tags.all()]
            ).exclude(id=self.id).annotate(frequency=models.Count('id')).order_by('-frequency')[:count]
            cache.set(cache_key, related_list, 60 * 60)

        from .base import load_cached
        loaded = load_cached(Question, [r['id'] for r in related_list])
        return [loaded[r['id']] for r in related_list if isinstance(loaded.get(r['id']), Question)]




class QuestionSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    question = models.ForeignKey(Node, on_delete=models.CASCADE)
    auto_subscription = models.BooleanField(default=True)
    last_view = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        indexes = [models.Index(fields=['user', 'last_view'], name='subscription_user_view_idx'),
                   # Looked up on every signed-in question view.
                   models.Index(fields=['question', 'user'], name='subscription_question_user_idx')]


class QuestionRevision(NodeRevision):
    class Meta:
        proxy = True

