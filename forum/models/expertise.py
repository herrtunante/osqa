import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _

from .node import Node
from .tag import Tag
from .user import User


class TagExpertise(models.Model):
    """How well a member answers questions in one tag (expert routing,
    forum/utils/expertise.py, which documents the formula). Rebuilt by
    `manage.py refresh_tag_expertise` (daily) and updated for the answerer
    when one of their answers is accepted."""

    user = models.ForeignKey(User, related_name='tag_expertise', on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, related_name='expertise', on_delete=models.CASCADE)
    score = models.FloatField(default=0)
    # Visible answers on questions with this tag, and how many of them were accepted.
    answers = models.PositiveIntegerField(default=0)
    accepted = models.PositiveIntegerField(default=0)
    updated = models.DateTimeField(default=datetime.datetime.now)

    class Meta:
        app_label = 'forum'
        ordering = ('-score', 'id')
        constraints = [models.UniqueConstraint(fields=['user', 'tag'], name='tagexpertise_user_tag_unique')]
        indexes = [models.Index(fields=['tag', 'score'], name='tagexpertise_tag_score_idx')]

    def __str__(self):
        return '%s in %s: %.1f' % (self.user_id, self.tag_id, self.score)


class AnswerRequest(models.Model):
    """A member was asked to answer a question because of their expertise in
    one of its tags (forum/utils/expertise.py). One per member and question,
    so nobody is asked twice; routed ones count towards the daily cap. "Not
    for me" on a question that was only listed on the member's page records
    a declined row with routed=False."""

    PENDING, ANSWERED, DECLINED = 'pending', 'answered', 'declined'
    STATES = (
        (PENDING, _('Waiting')),
        (ANSWERED, _('Answered')),
        (DECLINED, _('Not for me')),
    )

    question = models.ForeignKey(Node, related_name='+', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='answer_requests', on_delete=models.CASCADE)
    # Why: the member's best tag among the question's, and their answers in it.
    tag_name = models.CharField(max_length=255, blank=True)
    tag_answers = models.PositiveIntegerField(default=0)
    score = models.FloatField(default=0)
    state = models.CharField(max_length=16, choices=STATES, default=PENDING)
    routed = models.BooleanField(default=True)
    created = models.DateTimeField(default=datetime.datetime.now)
    responded = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('-created', '-id')
        constraints = [models.UniqueConstraint(fields=['question', 'user'], name='answerrequest_question_user_unique')]
        indexes = [models.Index(fields=['user', 'created'], name='answerrequest_user_date_idx'),
                   models.Index(fields=['user', 'state'], name='answerrequest_user_state_idx')]

    def __str__(self):
        return 'Answer request %s: %s for %s (%s)' % (self.id, self.user_id, self.question_id, self.state)
