from django.utils.translation import gettext as _
from .base import *

class Vote(models.Model):
    user = models.ForeignKey(User, related_name="votes", on_delete=models.CASCADE)
    node = models.ForeignKey(Node, related_name="votes", on_delete=models.CASCADE)
    value = models.SmallIntegerField()
    action = models.OneToOneField(Action, related_name="vote", on_delete=models.CASCADE)
    voted_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        unique_together = ('user', 'node')


class Flag(models.Model):
    user = models.ForeignKey(User, related_name="flags", on_delete=models.CASCADE)
    node = models.ForeignKey(Node, related_name="flags", on_delete=models.CASCADE)
    reason = models.CharField(max_length=300)
    action = models.OneToOneField(Action, related_name="flag", on_delete=models.CASCADE)
    flagged_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        unique_together = ('user', 'node')


class Badge(BaseModel):
    GOLD = 1
    SILVER = 2
    BRONZE = 3

    type        = models.SmallIntegerField()
    cls         = models.CharField(max_length=50, null=True)
    awarded_count = models.PositiveIntegerField(default=0)

    awarded_to    = models.ManyToManyField(User, through='Award', related_name='badges')

    def get_handler(self):
        from forum.badges import BadgesMeta
        return BadgesMeta.by_class.get(self.cls, None)

    @property
    def name(self):
        cls = self.get_handler()
        return cls and cls.name or _("Unknown")

    @property
    def description(self):
        cls = self.get_handler()
        return cls and cls.description or _("No description available")
    def get_absolute_url(self):
        return reverse('badge', kwargs={'id': self.id, 'slug': slugify(self.name)})

    def save(self, *args, **kwargs):
        if isinstance(self.awarded_count, models.expressions.Expression):
            super().save(*args, **kwargs)
            self.awarded_count = self.__class__.objects.filter(id=self.id).values_list('awarded_count', flat=True)[0]
        else:
            super().save(*args, **kwargs)


    class Meta:
        app_label = 'forum'
        base_manager_name = 'objects'


class Award(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    badge = models.ForeignKey('Badge', related_name="awards", on_delete=models.CASCADE)
    node = models.ForeignKey(Node, null=True, on_delete=models.CASCADE)

    awarded_at = models.DateTimeField(default=datetime.datetime.now)

    trigger = models.ForeignKey(Action, related_name="awards", null=True, on_delete=models.CASCADE)
    action = models.OneToOneField(Action, related_name="award", on_delete=models.CASCADE)


    class Meta:
        unique_together = ('user', 'badge', 'node')
        app_label = 'forum'
        indexes = [models.Index(fields=['awarded_at'], name='award_awarded_at_idx')]
