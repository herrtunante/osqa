"""How often activity emails are sent: as it happens, in a daily or weekly
summary, or never (SubscriptionSettings.email_frequency).

forum/subscriptions.py hands every activity email (new answer, comment,
mention, accepted answer, new question, new member) to deliver(), after its
own checks (preferences, who follows what). deliver() then
- emails the members who get emails as it happens, right away;
- queues a PendingEmail row for those with a daily or weekly summary;
- drops it for "never".
Mentions go out right away whatever the frequency while the member keeps
SubscriptionSettings.mentions_immediate on (the default).

send_summaries() (send_email_alerts, run daily from cron) sends each member
with queued rows one summary email and deletes the rows it covered:
- daily members: on every run, at most one summary a day;
- weekly members: on the WEEKLY_SUMMARY_DAY weekday (Monday by default), or
  on the first run after it when that day's run was missed (LAST_WEEKLY in
  EMAIL_DIGEST_FLAG is a week old or more);
- rows are claimed (deleted) before the email is sent, so running the command
  twice sends nothing twice, and they stay queued until a run sends them, so
  a missed run only delays them: a summary covers everything since the last.
Members who went back to "as it happens" get their leftovers in one last
summary; "never", notifications switched off or a suspended account drop
them. Nothing is sent when there's nothing new (or only deleted posts).
"""
import datetime
from collections import OrderedDict

from django.db.models import Q
from django.utils.dates import WEEKDAYS
from django.utils.translation import gettext_lazy as _lazy

from forum import settings

IMMEDIATE, DAILY, WEEKLY, NEVER = 'i', 'd', 'w', 'n'

# The choice on the email settings page: value, name, one-line explanation.
FREQUENCY_CHOICES = (
    (IMMEDIATE, _lazy("As it happens")),
    (DAILY, _lazy("Daily summary")),
    (WEEKLY, _lazy("Weekly summary")),
    (NEVER, _lazy("Never")),
)

# Items shown per section of a summary; the rest are counted ("And 3 more").
SECTION_LIMIT = 10

# Summary sections in the order they are shown.
SECTIONS = (
    ('mention', _lazy("Mentions")),
    ('answer_own', _lazy("Answers to your questions")),
    ('answer', _lazy("New answers on questions you follow")),
    ('comment', _lazy("New comments")),
    ('accepted', _lazy("Accepted answers")),
    ('new_question', _lazy("New questions")),
    ('member', _lazy("New members")),
)


def weekly_day():
    """The weekday of the weekly summary (0 = Monday)."""
    try:
        day = int(str(settings.WEEKLY_SUMMARY_DAY))
    except (TypeError, ValueError):
        return 0
    return day if 0 <= day <= 6 else 0


def weekly_day_name():
    return WEEKDAYS[weekly_day()]


def frequency_hints():
    """value -> one-line explanation of each frequency (settings page)."""
    day = weekly_day_name()
    return {
        IMMEDIATE: _lazy("A separate email for each answer, comment or new question, as soon as it's posted."),
        DAILY: _lazy("One email a day with everything new. No news, no email."),
        WEEKLY: _lazy("One email every %(day)s with everything new. No news, no email.") % {'day': day},
        NEVER: _lazy("No activity emails. Emails about your account, such as sign-in links, still arrive."),
    }


# -- sending or queueing ------------------------------------------------------

def deliver(users, kind, template, context, node=None, actor=None):
    """Hands an activity email of `kind` (a PendingEmail kind) to the members
    in the queryset `users`, according to how often each wants emails.
    Returns (members emailed now, members it was queued for)."""
    from forum.models import PendingEmail
    from forum.utils.mail import send_template_email

    now = Q(subscription_settings__email_frequency=IMMEDIATE)
    later = users.filter(subscription_settings__email_frequency__in=(DAILY, WEEKLY))
    if kind == PendingEmail.MENTION:
        now |= Q(subscription_settings__mentions_immediate=True)
        later = later.filter(subscription_settings__mentions_immediate=False)

    immediate = list(users.filter(now))
    if immediate:
        send_template_email(immediate, template, context)
    queued = list(later)
    if queued:
        PendingEmail.objects.bulk_create([PendingEmail(user=user, kind=kind, node=node, actor=actor)
                                          for user in queued])
    return immediate, queued


# -- the summary ----------------------------------------------------------------

class Summary:
    """What one summary email shows: sections (title, items, how many more)."""

    def __init__(self, items, period):
        self.period = period
        self.count = len(items)
        self.sections = []
        for key, title in SECTIONS:
            found = [item for item in items if item['section'] == key]
            if found:
                self.sections.append({'key': key, 'title': title, 'items': found[:SECTION_LIMIT],
                                      'count': len(found), 'more': max(0, len(found) - SECTION_LIMIT)})


def summary_item(user, kind, node=None, actor=None):
    """One line of a summary (a dict for the template), or None when the post
    or the person is gone."""
    if kind == 'member':
        if actor is None or not actor.is_active:
            return None
        return {'section': 'member', 'kind': kind, 'actor': actor, 'url': actor.get_profile_url()}
    if node is None:
        return None
    node = node.leaf
    question = node.absolute_parent.leaf
    if node.nis.deleted or question.nis.deleted:
        return None
    section = kind
    if kind == 'answer' and question.author_id == user.id:
        section = 'answer_own'
    return {'section': section, 'kind': kind, 'node': node, 'actor': actor, 'question': question,
            'url': node.get_absolute_url(), 'own': node.author_id == user.id}


def build_summary(user, rows, period):
    """The Summary of a member's PendingEmail rows, or None when nothing in
    them can still be shown."""
    items = [item for item in (summary_item(user, row.kind, row.node, row.actor) for row in rows) if item]
    return Summary(items, period) if items else None


def weekly_run_due(now, last_weekly):
    """Weekly summaries go out on the configured weekday, or on the next run
    when that day's run was missed."""
    if now.weekday() == weekly_day():
        return True
    return isinstance(last_weekly, datetime.datetime) and now - last_weekly >= datetime.timedelta(days=7)


def send_summaries(now=None):
    """Sends the daily and weekly summaries that are due. Returns
    {'daily': n, 'weekly': n} (summaries sent)."""
    from forum.models import PendingEmail, SubscriptionSettings
    from forum.settings.email import EMAIL_DIGEST_FLAG
    from forum.utils.mail import send_template_email

    now = now or datetime.datetime.now()
    control = EMAIL_DIGEST_FLAG.value if isinstance(EMAIL_DIGEST_FLAG.value, dict) else {}
    weekly = weekly_run_due(now, control.get('LAST_WEEKLY'))
    if weekly:
        EMAIL_DIGEST_FLAG.set_value(dict(control, LAST_WEEKLY=now))

    rows = PendingEmail.objects.filter(created_at__lte=now).select_related(
        'user', 'user__subscription_settings', 'actor', 'node').order_by('user_id', 'created_at', 'id')
    by_user = OrderedDict()
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)

    dropped, batches = [], {DAILY: OrderedDict(), WEEKLY: OrderedDict()}
    for user_rows in by_user.values():
        user = user_rows[0].user
        prefs = user.subscription_settings
        ids = [row.id for row in user_rows]
        if not user.is_active or not prefs.enable_notifications or prefs.email_frequency == NEVER:
            dropped += ids
            continue
        if prefs.last_summary_at and prefs.last_summary_at.date() >= now.date():
            continue  # one summary a day at most; the rest waits for the next one
        period = WEEKLY if prefs.email_frequency == WEEKLY else DAILY
        if period == WEEKLY and not weekly:
            continue
        summary = build_summary(user, user_rows, period)
        # Claim the rows first: a second run (or one running at the same time) can't send them again.
        if not PendingEmail.objects.filter(id__in=ids).delete()[0] or summary is None:
            continue
        SubscriptionSettings.objects.filter(id=prefs.id).update(last_summary_at=now)
        batches[period][user] = summary

    if dropped:
        PendingEmail.objects.filter(id__in=dropped).delete()

    for period, found in batches.items():
        if found:
            send_template_email(list(found), 'notifications/summary.html', {
                'summaries': {user.id: summary for user, summary in found.items()}, 'period': period,
                'email_kind': 'summary_weekly' if period == WEEKLY else 'summary_daily',
            })
    return {'daily': len(batches[DAILY]), 'weekly': len(batches[WEEKLY])}
