import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _

from .node import Node
from .user import User


class Notification(models.Model):
    """Something a user should see in the forum itself (the bell in the
    header): replies, mentions, accepted answers, badges. Independent of the
    email preferences; created by forum/notifications.py."""

    ANSWER, COMMENT, MENTION, ACCEPTED, BADGE, REMINDER = 'answer', 'comment', 'mention', 'accepted', 'badge', 'reminder'
    # A post held for review (forum/utils/review.py) was approved or removed.
    APPROVED, REMOVED = 'approved', 'removed'
    KINDS = (
        (ANSWER, _('New answer')),
        (COMMENT, _('New comment')),
        (MENTION, _('Mention')),
        (ACCEPTED, _('Answer accepted')),
        (BADGE, _('Badge')),
        (REMINDER, _('Accept an answer')),
        (APPROVED, _('Post approved')),
        (REMOVED, _('Post removed')),
    )

    user = models.ForeignKey(User, related_name='notifications', on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=KINDS)
    actor = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    node = models.ForeignKey(Node, null=True, blank=True, related_name='+', on_delete=models.CASCADE)
    # Badge name for badge notifications (the award has no post).
    text = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=datetime.datetime.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=['user', 'read_at'], name='notification_unread_idx'),
                   models.Index(fields=['user', 'created_at'], name='notification_user_date_idx')]

    @property
    def is_unread(self):
        return self.read_at is None


class QuestionReminder(models.Model):
    """The asker was reminded to accept an answer (sent once per question)."""
    question = models.OneToOneField(Node, related_name='+', on_delete=models.CASCADE)
    sent_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'


class PendingEmail(models.Model):
    """An activity email held back for a member's daily or weekly summary
    (SubscriptionSettings.email_frequency). Queued by forum/subscriptions.py
    instead of the immediate email; send_email_alerts sends one summary per
    member and deletes the rows it covered."""

    ANSWER, COMMENT, MENTION, ACCEPTED, NEW_QUESTION, MEMBER = (
        'answer', 'comment', 'mention', 'accepted', 'new_question', 'member')
    KINDS = (
        (ANSWER, _('New answer')),
        (COMMENT, _('New comment')),
        (MENTION, _('Mention')),
        (ACCEPTED, _('Answer accepted')),
        (NEW_QUESTION, _('New question')),
        (MEMBER, _('New member')),
    )

    user = models.ForeignKey(User, related_name='pending_emails', on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=KINDS)
    # Who did it (the answerer, the person who accepted, the new member...).
    actor = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.CASCADE)
    node = models.ForeignKey(Node, null=True, blank=True, related_name='+', on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        ordering = ('created_at', 'id')
        indexes = [models.Index(fields=['user', 'created_at'], name='pendingemail_user_date_idx')]
