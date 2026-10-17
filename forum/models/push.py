import datetime

from django.db import models

from .user import User


class PushSubscription(models.Model):
    """A browser (one device) that receives Web Push notifications for a
    member: the push service URL and the keys the browser gave for encrypting
    messages to it (forum/utils/push.py). Deleted when the member removes the
    device, turns the switch off there, or the push service says it is gone."""

    user = models.ForeignKey(User, related_name='push_subscriptions', on_delete=models.CASCADE)
    # Push service URLs can be long (FCM ~ 200 characters, others more).
    endpoint = models.TextField()
    # Hash of the endpoint, for the unique index (MySQL can't index a TEXT column).
    endpoint_hash = models.CharField(max_length=64, unique=True)
    p256dh = models.CharField(max_length=128)
    auth = models.CharField(max_length=64)
    # "Chrome on Android", shown in the device list.
    label = models.CharField(max_length=64, blank=True)
    created = models.DateTimeField(default=datetime.datetime.now)
    last_success = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'forum'
        ordering = ('-created', '-id')

    def __str__(self):
        return '%s: %s' % (self.user_id, self.label or self.endpoint[:40])

    def info(self):
        """What pywebpush expects."""
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}
