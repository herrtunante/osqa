"""Figures for the staff support dashboard (Admin > Support dashboard) and
the questions remind_unaccepted reminds askers about.

A question "has a response" once someone other than the asker answered it;
response time is the time to that first answer. Deleted questions and
answers never count; closed questions are left out of the queues.
"""
import datetime
import statistics

from django.db.models import F, Min

from forum import settings
from forum.models import Answer, Question, QuestionReminder, Tag

DELETED = '(deleted)'
CLOSED = '(closed)'

# How far back the reminder looks: older questions (e.g. years of history
# imported from an old forum) are never reminded about.
REMINDER_WINDOW_DAYS = 60


def live_questions():
    return Question.objects.exclude(state_string__contains=DELETED)


def live_answers():
    return Answer.objects.exclude(state_string__contains=DELETED)


def support_tags():
    names = str(settings.SUPPORT_TAGS).replace(',', ' ').split()
    if names:
        return names
    return list(Tag.active.order_by('-used_count').values_list('name', flat=True)[:8])


def first_answers(questions):
    """{question id: time of the first answer by someone other than the asker}."""
    rows = live_answers().filter(parent__in=questions).exclude(author_id=F('parent__author_id')) \
        .values('parent_id').annotate(first=Min('added_at'))
    return {row['parent_id']: row['first'] for row in rows}


def accepted_ids(questions):
    return set(live_answers().filter(parent__in=questions, marked=True).values_list('parent_id', flat=True))


def hours(delta):
    return delta.total_seconds() / 3600.0


def summary(questions):
    """Counts and response times for a set of questions."""
    rows = list(questions.values_list('id', 'added_at'))
    ids = [row[0] for row in rows]
    first = first_answers(questions)
    accepted = accepted_ids(questions)
    waits = [hours(first[qid] - added) for qid, added in rows if qid in first]
    total = len(ids)
    return {
        'asked': total,
        'answered': len(first),
        'unanswered': total - len(first),
        'accepted': len(accepted),
        'answered_percent': round(100.0 * len(first) / total) if total else None,
        'accepted_percent': round(100.0 * len(accepted) / total) if total else None,
        'median_hours': statistics.median(waits) if waits else None,
    }


def period_questions(days, tag=None):
    questions = live_questions()
    if days:
        questions = questions.filter(added_at__gte=datetime.datetime.now() - datetime.timedelta(days=days))
    if tag:
        questions = questions.filter(tags__name=tag)
    return questions


def unanswered_queue(days=None, tag=None, limit=100):
    """Open questions nobody but the asker has answered, oldest first."""
    questions = period_questions(days, tag).exclude(state_string__contains=CLOSED)
    answered = live_answers().filter(parent__in=questions).exclude(author_id=F('parent__author_id')) \
        .values('parent_id')
    return questions.exclude(id__in=answered).order_by('added_at').select_related('author')[:limit]


def unaccepted_queue(days=None, tag=None, limit=100):
    """Open questions with an answer from someone else but none accepted,
    longest waiting first."""
    questions = period_questions(days, tag).exclude(state_string__contains=CLOSED)
    first = first_answers(questions)
    accepted = accepted_ids(questions)
    waiting = [qid for qid in first if qid not in accepted]
    items = list(Question.objects.filter(id__in=waiting).select_related('author'))
    for question in items:
        question.first_answer_at = first[question.id]
    items.sort(key=lambda q: q.first_answer_at)
    return items[:limit]


def reminder_candidates(now=None):
    """Questions whose asker should be reminded to accept an answer."""
    days = int(settings.ACCEPT_REMINDER_DAYS)
    if days <= 0:
        return []
    now = now or datetime.datetime.now()
    questions = live_questions().exclude(state_string__contains=CLOSED) \
        .filter(added_at__gte=now - datetime.timedelta(days=REMINDER_WINDOW_DAYS)) \
        .exclude(id__in=QuestionReminder.objects.values('question_id'))
    first = first_answers(questions)
    accepted = accepted_ids(questions)
    due = [qid for qid, at in first.items() if qid not in accepted and at <= now - datetime.timedelta(days=days)]
    return list(Question.objects.filter(id__in=due).select_related('author').order_by('added_at'))


def format_hours(value):
    if value is None:
        return ''
    if value < 1:
        return '%d min' % max(1, round(value * 60))
    if value < 48:
        return '%.0f h' % value if value >= 10 else '%.1f h' % value
    return '%.1f d' % (value / 24)
