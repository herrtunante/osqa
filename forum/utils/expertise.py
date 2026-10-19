"""Expert routing: a new question reaches the members most likely to answer
it well ("Questions for you").

Expertise (TagExpertise, one row per member and tag)
----------------------------------------------------
Counted from a member's answers that others can see: not deleted, not held for
review, on a question that is neither deleted nor held, and not on their own
question. Each such answer adds to every tag of its question:

    points = max(0, 1 + 0.5 * votes) + (3 if accepted else 0)

where `votes` is the answer's score clipped to [-2, 10] (so a downvoted answer
adds nothing and votes stop counting after ten), and answers posted more than
RECENT_DAYS (a year) ago count OLD_WEIGHT (a quarter) of their points. A
member's expertise in a tag is the sum, kept with how many answers and
accepted answers it comes from. Only active members get rows.

`manage.py refresh_tag_expertise` (daily, in CRON_COMMANDS) rebuilds the table
with refresh_all(); when an answer is accepted, refresh_user() recomputes the
answerer's rows for that question's tags only (forum/expert_routing.py).

Routing (AnswerRequest)
-----------------------
When a question becomes public (asked and published, or a held first post
approved: forum/expert_routing.py replays the ask hook like
forum/notifications.py), route_question() asks the members with the highest
expertise summed over its tags, at most EXPERT_ROUTING_MAX_EXPERTS, whose best
tag reaches EXPERT_ROUTING_MIN_SCORE. Never the asker, inactive members,
members who already answered or were asked (one AnswerRequest per member and
question), members who turned "Suggest questions I could answer" off
(user.prop.preferences['answer_requests']) or members asked
EXPERT_ROUTING_DAILY_CAP times in the last 24 hours. Each one gets an in-site
notification (kind answer_request, text "<answers>:<tag>" for the reason) and
the email notifications/answer_request.html through deliver() (as often as
they want activity emails). Answering marks the request answered; "Not for me"
(decline()) marks it declined, which also hides the question from their page.

The "Questions for you" page (forum/views/for_you.py) lists open questions
without an accepted answer in the member's top tags, routed requests first.
"""
import datetime
import logging

from django.db.models import Count, F

from forum import settings

log = logging.getLogger('forum.expertise')

RECENT_DAYS = 365
OLD_WEIGHT = 0.25
ACCEPTED_POINTS = 3
VOTE_POINTS = 0.5
VOTES_MIN, VOTES_MAX = -2, 10
# Rows of TagExpertise looked at per question (the best ones in its tags).
CANDIDATE_ROWS = 500
# The member's best tags used by the "Questions for you" page.
TOP_TAGS = 10
PREFERENCE = 'answer_requests'
CAP_HOURS = 24


# -- expertise ----------------------------------------------------------------

def answer_points(score, accepted, added_at, now):
    votes = max(VOTES_MIN, min(int(score or 0), VOTES_MAX))
    points = max(0.0, 1 + VOTE_POINTS * votes) + (ACCEPTED_POINTS if accepted else 0)
    if added_at is not None and added_at < now - datetime.timedelta(days=RECENT_DAYS):
        points *= OLD_WEIGHT
    return points


def counted_answers(user_ids=None, tag_ids=None):
    """(author id, tag id, score, accepted, added at) of every answer that
    counts, once per tag of its question."""
    from forum.models import Answer
    from forum.utils import review
    answers = Answer.objects.public().filter(author__is_active=True).exclude(
        review.hidden_q('parent__')).exclude(author_id=F('parent__author_id'))
    if user_ids is not None:
        answers = answers.filter(author_id__in=list(user_ids))
    # One filter() call: a second one on the many-to-many would join the tags again (rows twice).
    answers = answers.filter(parent__tags__in=list(tag_ids)) if tag_ids is not None else \
        answers.filter(parent__tags__isnull=False)
    return answers.order_by().values_list('author_id', 'parent__tags', 'score', 'marked', 'added_at')


def compute(rows, now=None):
    """{(user id, tag id): [score, answers, accepted]} from counted_answers() rows."""
    now = now or datetime.datetime.now()
    totals = {}
    for user_id, tag_id, score, accepted, added_at in rows:
        entry = totals.setdefault((user_id, tag_id), [0.0, 0, 0])
        entry[0] += answer_points(score, accepted, added_at, now)
        entry[1] += 1
        entry[2] += 1 if accepted else 0
    return totals


def refresh_all(now=None):
    """Rebuilds TagExpertise from scratch. Returns the number of rows."""
    from django.db import transaction
    from forum.models import TagExpertise
    now = now or datetime.datetime.now()
    totals = compute(counted_answers().iterator(), now)
    rows = [TagExpertise(user_id=user_id, tag_id=tag_id, score=round(score, 2), answers=answers,
                         accepted=accepted, updated=now)
            for (user_id, tag_id), (score, answers, accepted) in totals.items() if score > 0]
    with transaction.atomic():
        TagExpertise.objects.all().delete()
        TagExpertise.objects.bulk_create(rows, batch_size=500)
    return len(rows)


def refresh_user(user, tag_ids, now=None):
    """Recomputes `user`'s expertise in `tag_ids` only (after an accepted answer)."""
    from forum.models import TagExpertise
    tag_ids = list(tag_ids)
    if not tag_ids:
        return
    now = now or datetime.datetime.now()
    totals = compute(counted_answers(user_ids=[user.id], tag_ids=tag_ids), now)
    for tag_id in tag_ids:
        score, answers, accepted = totals.get((user.id, tag_id), (0, 0, 0))
        if score > 0 and user.is_active:
            TagExpertise.objects.update_or_create(user_id=user.id, tag_id=tag_id, defaults=dict(
                score=round(score, 2), answers=answers, accepted=accepted, updated=now))
        else:
            TagExpertise.objects.filter(user_id=user.id, tag_id=tag_id).delete()


def top_tags(user, limit=TOP_TAGS):
    """The member's TagExpertise rows with the most expertise (tag loaded)."""
    from forum.models import TagExpertise
    return list(TagExpertise.objects.filter(user_id=user.id, score__gt=0).select_related('tag')
                .order_by('-score', 'id')[:limit])


# -- the member's choice --------------------------------------------------------

def requests_enabled(user):
    """Whether the member wants questions suggested (on unless turned off)."""
    preferences = user.prop.preferences or {}
    return preferences.get(PREFERENCE, True) is not False


def opted_out(user_ids):
    """The ids among `user_ids` of members who turned suggestions off (one query)."""
    from forum.models.user import UserProperty
    found = set()
    for user_id, value in UserProperty.objects.filter(user_id__in=list(user_ids), key='preferences') \
            .values_list('user_id', 'value'):
        if isinstance(value, dict) and value.get(PREFERENCE, True) is False:
            found.add(user_id)
    return found


# -- routing ------------------------------------------------------------------

def daily_cap():
    try:
        return max(0, int(settings.EXPERT_ROUTING_DAILY_CAP))
    except (TypeError, ValueError):
        return 3


def pick_experts(question, now=None):
    """[{'user_id', 'score', 'tag', 'answers'}] of the members to ask about
    `question`, best first (see the module docstring for who is left out)."""
    from forum.models import Answer, AnswerRequest, TagExpertise
    now = now or datetime.datetime.now()
    limit = int(settings.EXPERT_ROUTING_MAX_EXPERTS or 0)
    cap = daily_cap()
    tag_ids = list(question.tags.values_list('id', flat=True))
    if limit <= 0 or cap <= 0 or not tag_ids:
        return []
    min_score = float(settings.EXPERT_ROUTING_MIN_SCORE or 0)

    rows = TagExpertise.objects.filter(tag_id__in=tag_ids, user__is_active=True, score__gt=0) \
        .exclude(user_id=question.author_id).select_related('tag').order_by('-score', 'id')[:CANDIDATE_ROWS]
    candidates = {}
    for row in rows:
        entry = candidates.setdefault(row.user_id, {'user_id': row.user_id, 'score': 0.0, 'best': row})
        entry['score'] += row.score
        if row.score > entry['best'].score:
            entry['best'] = row
    candidates = {user_id: entry for user_id, entry in candidates.items() if entry['best'].score >= min_score}
    if not candidates:
        return []

    ids = list(candidates)
    excluded = set(AnswerRequest.objects.filter(question_id=question.id).values_list('user_id', flat=True))
    excluded |= set(Answer.objects.filter(parent_id=question.id, author_id__in=ids).values_list('author_id', flat=True))
    excluded |= opted_out(ids)
    recent = AnswerRequest.objects.filter(user_id__in=ids, routed=True, created__gte=now - datetime.timedelta(hours=CAP_HOURS)) \
        .values('user_id').annotate(n=Count('id')).values_list('user_id', 'n')
    excluded |= {user_id for user_id, n in recent if n >= cap}

    chosen = sorted((entry for user_id, entry in candidates.items() if user_id not in excluded),
                    key=lambda entry: (-entry['score'], entry['user_id']))[:limit]
    return [{'user_id': entry['user_id'], 'score': round(entry['score'], 2), 'tag': entry['best'].tag.name,
             'answers': entry['best'].answers} for entry in chosen]


def route_question(question, now=None):
    """Asks the experts about a question that just became public. Returns
    the AnswerRequests created."""
    from forum.models import AnswerRequest, Notification, PendingEmail, Question, User
    if not settings.EXPERT_ROUTING_ENABLED:
        return []
    question = Question.objects.filter(id=question.id).first()
    if question is None or not question.is_notifiable or question.nis.deleted or question.nis.closed:
        return []
    # Routed once (a replayed hook routes nobody else).
    if AnswerRequest.objects.filter(question_id=question.id, routed=True).exists():
        return []
    now = now or datetime.datetime.now()
    created = []
    for pick in pick_experts(question, now):
        request, new = AnswerRequest.objects.get_or_create(question_id=question.id, user_id=pick['user_id'], defaults=dict(
            tag_name=pick['tag'][:255], tag_answers=pick['answers'], score=pick['score'], created=now))
        if new:
            created.append(request)
    if not created:
        return []

    from forum.notifications import notify
    from forum.subscriptions import filter_subscribers
    from forum.utils.email_frequency import deliver
    for request in created:
        notify({request.user_id}, Notification.ANSWER_REQUEST, question.author, question,
               text='%d:%s' % (request.tag_answers, request.tag_name))
    users = filter_subscribers(User.objects.filter(id__in=[r.user_id for r in created],
                                                   subscription_settings__enable_notifications=True))
    deliver(users, PendingEmail.ANSWER_REQUEST, 'notifications/answer_request.html', {
        'question': question, 'email_kind': 'answer_request',
        'reasons': {r.user_id: {'answers': r.tag_answers, 'tag': r.tag_name} for r in created},
    }, node=question, actor=question.author)
    log.info('question %s routed to %d expert(s)', question.id, len(created))
    return created


def answered(question_id, user_id, now=None):
    """The member answered a question they were asked about."""
    from forum.models import AnswerRequest
    return AnswerRequest.objects.filter(question_id=question_id, user_id=user_id, state=AnswerRequest.PENDING) \
        .update(state=AnswerRequest.ANSWERED, responded=now or datetime.datetime.now())


def decline(user, question, now=None):
    """"Not for me": no request for this question ever again, and it leaves
    the member's page (a pending request is declined; a question that was only
    listed gets a declined, not routed, row). Returns False when the member
    had already answered or declined it."""
    from forum.models import AnswerRequest, Notification
    from forum import notifications
    now = now or datetime.datetime.now()
    request, new = AnswerRequest.objects.get_or_create(question_id=question.id, user_id=user.id, defaults=dict(
        state=AnswerRequest.DECLINED, routed=False, created=now, responded=now))
    if not new:
        if request.state != AnswerRequest.PENDING:
            return False
        AnswerRequest.objects.filter(id=request.id).update(state=AnswerRequest.DECLINED, responded=now)
    notifications.mark_read(user, kind=Notification.ANSWER_REQUEST, node_id=question.id)
    return True
