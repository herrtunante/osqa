import datetime
from .base import *

from django.conf import settings as django_settings
from django.core.cache.backends.base import BaseCache
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.encoding import smart_str, force_str

from forum import modules

class ActiveTagManager(CachedManager):
    use_for_related_fields = True

    def get_queryset(self):
        return super(ActiveTagManager, self).get_queryset().exclude(used_count__lt=1)

class Tag(BaseModel):
    name            = models.CharField(max_length=255, unique=True)
    created_by      = models.ForeignKey(User, related_name='created_tags', on_delete=models.CASCADE)
    created_at      = models.DateTimeField(default=datetime.datetime.now, blank=True, null=True)
    marked_by       = models.ManyToManyField(User, related_name="marked_tags", through="MarkedTag")
    # Short Markdown text explaining what the tag is for (shown on the tag's page).
    description = models.TextField(blank=True, default='')
    # Denormalised data
    used_count = models.PositiveIntegerField(default=0)

    active = ActiveTagManager()

    class Meta:
        ordering = ('-used_count', 'name')
        app_label = 'forum'
        indexes = [models.Index(fields=['used_count', 'name'], name='tag_used_count_idx')]

    def __str__(self):
        return force_str(self.name)

    def add_to_usage_count(self, value):
        if self.used_count + value < 0:
            self.used_count = 0
        else:
            self.used_count = models.F('used_count') + value

    def cache_key(self):
        return self._generate_cache_key(Tag.safe_cache_name(self.name))

    @classmethod
    def safe_cache_name(cls, name):
        return "".join([str(ord(c)) for c in name])

    @classmethod
    def infer_cache_key(cls, querydict):
        if 'name' in querydict:
            cache_key = cls._generate_cache_key(cls.safe_cache_name(querydict['name']))

            if len(cache_key) > django_settings.CACHE_MAX_KEY_LENGTH:
                cache_key = cache_key[:django_settings.CACHE_MAX_KEY_LENGTH]

            return cache_key

        return None

    @classmethod
    def value_to_list_on_cache_query(cls):
        return 'name'
    def get_absolute_url(self):
        return reverse('tag_questions', kwargs={'tag': self.name})

    @property
    def description_html(self):
        if not self.description:
            return ''
        from forum.models.node import Node
        return Node._as_markdown(self.description, 'limitedsyntax')

    @property
    def description_excerpt(self):
        from django.utils.html import strip_tags
        from django.utils.text import Truncator
        return Truncator(' '.join(strip_tags(str(self.description_html)).split())).chars(140)


class TagSynonym(models.Model):
    """A tag name that is replaced by another tag ("gis-analysis" -> "gis")."""
    source_name = models.CharField(max_length=255, unique=True)
    target = models.ForeignKey(Tag, related_name='synonyms', on_delete=models.CASCADE)
    created_by = models.ForeignKey(User, related_name='created_tag_synonyms', null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        ordering = ('source_name',)

    def __str__(self):
        return '%s -> %s' % (self.source_name, self.target.name)

    @classmethod
    def resolve(cls, names):
        """Replace synonyms in a list of tag names by their targets, keeping order and dropping repeats."""
        mapping = dict(cls.objects.filter(source_name__in=names).values_list('source_name', 'target__name'))
        out = []
        for name in names:
            name = mapping.get(name, name)
            if name not in out:
                out.append(name)
        return out

class MarkedTag(models.Model):
    TAG_MARK_REASONS = (('good', _lazy('interesting')), ('bad', _lazy('ignored')))
    tag = models.ForeignKey(Tag, related_name='user_selections', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='tag_selections', on_delete=models.CASCADE)
    reason = models.CharField(max_length=16, choices=TAG_MARK_REASONS)

    class Meta:
        app_label = 'forum'
        # The tag selector and the ignored-tags filter.
        indexes = [models.Index(fields=['user', 'reason'], name='markedtag_user_reason_idx')]

