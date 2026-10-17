"""Figures for the community health dashboard (the admin dashboard, /admin/).

Every figure covers a period of the last 7, 30 or 90 days (a rolling window
ending now) and is compared with the period of the same length just before
it. What counts (the same rules everywhere, also explained on the page under
"How these are counted"):

- A post (question, answer, comment) counts when everybody can see it: not
  deleted, not held for review (forum/utils/review.py), not under a deleted or
  held question, and written by an account that isn't suspended or
  deactivated (User.is_active). Posts published while waiting for review
  ("after" mode) count.
- A question is answered by the first counted answer from someone other than
  the asker (as on the support dashboard, forum/utils/support.py). "Answered
  within 24 hours / 7 days" only looks at questions at least that old, so a
  question asked an hour ago doesn't lower the rate.
- Members are accounts that joined in the period and are active. "Posted
  within 7 days" only looks at members who joined at least 7 days ago.
- Active members posted or voted in the period; returning members were also
  active in the period before.
- Moderation: a flag is resolved when it is dismissed or its post deleted; a
  first post review when the post is approved or deleted; a suggested edit
  when a reviewer approves or rejects it.

The figures come from a dozen aggregate queries (no query per row) and are
kept in the shared cache for CACHE_SECONDS per period; `figures(days,
refresh=True)` recomputes them. Times are in the server's time zone
(TIME_ZONE). The labels are added when the page is drawn (`tiles()` and the
summaries), so the cached figures serve every language.
"""
import csv
import datetime
import math
import statistics

from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.utils import formats
from django.utils.dates import WEEKDAYS
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from forum.utils import review

PERIODS = (7, 30, 90)
DEFAULT_DAYS = 30
CACHE_SECONDS = 300
CACHE_KEY = 'community-health-v1-%d'

DAY_HOURS = 24
WEEK_HOURS = 7 * 24
CLOSED_MARK = '(closed)'

# Time to first answer, in hours: (key, upper bound); the last has none.
BUCKETS = (('1h', 1), ('3h', 3), ('6h', 6), ('24h', 24), ('3d', 72), ('7d', 168), ('more', None))
TOP_TAGS = 8
TOP_ANSWERERS = 10


def parse_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return days if days in PERIODS else DEFAULT_DAYS


# -- what counts ----------------------------------------------------------------

def posts(node_type=None):
    """Posts everybody can see, by accounts in good standing (see the module
    docstring). `node_type`: 'question', 'answer', 'comment' or None for all."""
    from forum.models import Node
    if node_type is None:
        qs = Node.objects.filter(node_type__in=review.POST_TYPES)
    else:
        qs = Node.objects.filter(node_type=node_type)
    qs = qs.exclude(review.hidden_q()).filter(author__is_active=True)
    if node_type != 'question':
        qs = qs.exclude(review.hidden_q('abs_parent__'))
    return qs


def _first_answer():
    """Subquery: when the question (OuterRef) got its first counted answer from someone else."""
    answers = posts('answer').filter(parent=OuterRef('pk')).exclude(author_id=OuterRef('author_id'))
    return Subquery(answers.order_by('added_at').values('added_at')[:1])


def _hours(delta):
    return max(0.0, delta.total_seconds() / 3600.0)


def _median(values):
    return statistics.median(values) if values else None


def _rate(num, den):
    return {'num': num, 'den': den}


# -- the figures ----------------------------------------------------------------

def figures(days, refresh=False, now=None):
    """The figures for the last `days` days, from the shared cache when they are fresh."""
    key = CACHE_KEY % days
    data = None if refresh else cache.get(key)
    if data is None:
        data = compute(days, now=now)
        cache.set(key, data, CACHE_SECONDS)
    return data


def compute(days, now=None):
    from forum.models import Action, ModerationReport, Node, SuggestedEdit, User, Vote

    now = now or datetime.datetime.now()
    span = datetime.timedelta(days=days)
    start, prev_start, prev2_start = now - span, now - 2 * span, now - 3 * span

    def in_cur(when):
        return when is not None and start <= when < now

    def in_prev(when):
        return when is not None and prev_start <= when < start

    data = {'days': days, 'computed_at': now, 'start': start, 'previous_start': prev_start}
    cur, prev = {}, {}

    # 1. Questions of both periods, with their first answer and whether one is accepted.
    accepted = Exists(posts('answer').filter(parent=OuterRef('pk'), marked=True))
    rows = list(posts('question').filter(added_at__gte=prev_start, added_at__lt=now)
                .annotate(first_answer_at=_first_answer(), has_accepted=accepted)
                .order_by().values_list('added_at', 'first_answer_at', 'has_accepted'))
    buckets = dict((key, 0) for key, limit in BUCKETS)
    buckets['none'] = 0
    heatmap = [[0] * 24 for weekday in range(7)]
    for figures_, check in ((cur, in_cur), (prev, in_prev)):
        mine = [row for row in rows if check(row[0])]
        waits = [_hours(first - added) for added, first, acc in mine if first is not None]
        figures_['questions'] = len(mine)
        figures_['median_first_answer'] = _median(waits)
        figures_['accepted'] = _rate(sum(1 for row in mine if row[2]), len(mine))
        for name, limit in (('answered_24h', DAY_HOURS), ('answered_7d', WEEK_HOURS)):
            old_enough = [row for row in mine if _hours(now - row[0]) >= limit]
            figures_[name] = _rate(sum(1 for added, first, acc in old_enough
                                       if first is not None and _hours(first - added) <= limit), len(old_enough))
        if figures_ is cur:
            for added, first, acc in mine:
                heatmap[added.weekday()][added.hour] += 1
                if first is None:
                    buckets['none'] += 1
                    continue
                wait = _hours(first - added)
                for key, limit in BUCKETS:
                    if limit is None or wait < limit:
                        buckets[key] += 1
                        break
    data['buckets'] = [(key, buckets[key]) for key, limit in BUCKETS] + [('none', buckets['none'])]
    data['heatmap'] = heatmap

    # 2. Answers and comments of both periods.
    for row in (posts().filter(node_type__in=('answer', 'comment'), added_at__gte=prev_start, added_at__lt=now)
                .order_by().values('node_type')
                .annotate(cur=Count('id', filter=Q(added_at__gte=start)),
                          prev=Count('id', filter=Q(added_at__lt=start)))):
        cur[row['node_type'] + 's'], prev[row['node_type'] + 's'] = row['cur'], row['prev']
    for name in ('answers', 'comments'):
        cur.setdefault(name, 0)
        prev.setdefault(name, 0)

    # 3. Posts per day of the period.
    daily = {}
    for row in (posts().filter(added_at__gte=start, added_at__lt=now).annotate(day=TruncDate('added_at'))
                .order_by().values('day', 'node_type').annotate(n=Count('id'))):
        daily.setdefault(row['day'], {})[row['node_type']] = row['n']

    # 4. The unanswered backlog now and when the period began.
    backlog = posts('question').exclude(state_string__contains=CLOSED_MARK).annotate(
        first_answer_at=_first_answer()).aggregate(
        now=Count('id', filter=Q(first_answer_at__isnull=True)),
        then=Count('id', filter=Q(added_at__lt=start) & (Q(first_answer_at__isnull=True)
                                                          | Q(first_answer_at__gte=start))))
    cur['backlog'], prev['backlog'] = backlog['now'], backlog['then']

    # 5. New members of both periods, with their first counted post.
    first_post = Subquery(posts().filter(author_id=OuterRef('pk')).order_by('added_at').values('added_at')[:1])
    members = list(User.objects.filter(is_active=True, date_joined__gte=prev_start, date_joined__lt=now)
                   .annotate(first_post_at=first_post).order_by()
                   .values_list('date_joined', 'email_isvalid', 'first_post_at'))
    week = datetime.timedelta(days=7)
    for figures_, check in ((cur, in_cur), (prev, in_prev)):
        mine = [row for row in members if check(row[0])]
        old_enough = [row for row in mine if row[0] <= now - week]
        figures_['members'] = len(mine)
        figures_['confirmed_email'] = _rate(sum(1 for row in mine if row[1]), len(mine))
        figures_['posted_7d'] = _rate(sum(1 for joined, valid, first in old_enough
                                          if first is not None and first <= joined + week), len(old_enough))
    for joined, valid, first in members:
        if in_cur(joined):
            day = daily.setdefault(joined.date(), {})
            day['member'] = day.get('member', 0) + 1

    # 6. Active and returning members.
    def active(since, until):
        posters = posts().filter(added_at__gte=since, added_at__lt=until).values('author_id')
        voters = Vote.objects.filter(voted_at__gte=since, voted_at__lt=until).values('user_id')
        return Q(id__in=posters) | Q(id__in=voters)

    people = User.objects.filter(is_active=True).aggregate(
        cur=Count('id', filter=active(start, now)),
        prev=Count('id', filter=active(prev_start, start)),
        returning=Count('id', filter=active(start, now) & active(prev_start, start)),
        returning_prev=Count('id', filter=active(prev_start, start) & active(prev2_start, prev_start)))
    cur['active_members'], prev['active_members'] = people['cur'], people['prev']
    cur['returning_members'], prev['returning_members'] = people['returning'], people['returning_prev']

    # 7. Flags resolved: dismissed (the flag cancelled) or the post deleted.
    deleted_at = Subquery(Action.objects.filter(node_id=OuterRef('node_id'), action_type='delete', canceled=False)
                          .order_by('action_date').values('action_date')[:1])
    flag_rows = (Action.objects.filter(action_type='flag').annotate(deleted_at=deleted_at)
                 .filter(Q(canceled=True, canceled_at__gte=prev_start) | Q(deleted_at__gte=prev_start))
                 .order_by().values_list('action_date', 'canceled_at', 'deleted_at'))
    _resolution(cur, prev, 'flag_resolution', flag_rows, in_cur, in_prev)

    # 8. First posts reviewed: approved or deleted.
    approved_at = Subquery(Action.objects.filter(node_id=OuterRef('node_id'), action_type='approvepost',
                                                 canceled=False).order_by('action_date').values('action_date')[:1])
    review_rows = (Action.objects.filter(action_type='queueforreview').annotate(
        approved_at=approved_at, deleted_at=deleted_at)
        .filter(Q(approved_at__gte=prev_start) | Q(deleted_at__gte=prev_start))
        .order_by().values_list('action_date', 'approved_at', 'deleted_at'))
    _resolution(cur, prev, 'review_resolution', review_rows, in_cur, in_prev)

    # 9. Suggested edits reviewed.
    edit_rows = list(SuggestedEdit.objects.filter(
        status__in=(SuggestedEdit.APPROVED, SuggestedEdit.REJECTED), reviewed_at__gte=prev_start,
        reviewed_at__lt=now).order_by().values_list('created_at', 'reviewed_at'))
    for figures_, check in ((cur, in_cur), (prev, in_prev)):
        waits = [_hours(done - created) for created, done in edit_rows if check(done)]
        figures_['edits_reviewed'] = len(waits)
        figures_['edit_resolution'] = _median(waits)

    # 10. Automated (AI) reports and 11. accounts suspended.
    reports = ModerationReport.objects.aggregate(
        cur=Count('id', filter=Q(created_at__gte=start, created_at__lt=now)),
        prev=Count('id', filter=Q(created_at__gte=prev_start, created_at__lt=start)),
        open=Count('id', filter=Q(resolved_at=None)))
    cur['ai_reports'], prev['ai_reports'] = reports['cur'], reports['prev']
    data['ai_reports_open'] = reports['open']
    suspended = Action.objects.filter(action_type='suspend', canceled=False).aggregate(
        cur=Count('id', filter=Q(action_date__gte=start, action_date__lt=now)),
        prev=Count('id', filter=Q(action_date__gte=prev_start, action_date__lt=start)))
    cur['suspensions'], prev['suspensions'] = suspended['cur'], suspended['prev']

    # 12. Tags of the questions of both periods.
    period_questions = posts('question').filter(added_at__gte=prev_start, added_at__lt=now).values('id')
    tags = list(Node.tags.through.objects.filter(node_id__in=period_questions).order_by()
                .values('tag__name').annotate(cur=Count('id', filter=Q(node__added_at__gte=start)),
                                              prev=Count('id', filter=Q(node__added_at__lt=start))))
    growing = [{'name': row['tag__name'], 'cur': row['cur'], 'prev': row['prev'],
                'change': row['cur'] - row['prev']} for row in tags if row['cur'] > row['prev']]
    growing.sort(key=lambda row: (-row['change'], -row['cur'], row['name']))
    data['tags'] = growing[:TOP_TAGS]

    # 13. Who answered most in the period.
    data['answerers'] = [
        {'id': row['author_id'], 'username': row['author__username'], 'answers': row['answers'],
         'accepted': row['accepted']}
        for row in posts('answer').filter(added_at__gte=start, added_at__lt=now).order_by()
        .values('author_id', 'author__username')
        .annotate(answers=Count('id'), accepted=Count('id', filter=Q(marked=True)))
        .order_by('-answers', '-accepted', 'author__username')[:TOP_ANSWERERS]]

    first_day = start.date()
    data['daily'] = []
    for offset in range((now.date() - first_day).days + 1):
        day = first_day + datetime.timedelta(days=offset)
        counts = daily.get(day, {})
        data['daily'].append({'day': day, 'questions': counts.get('question', 0), 'answers': counts.get('answer', 0),
                              'comments': counts.get('comment', 0), 'members': counts.get('member', 0)})
    data['cur'], data['prev'] = cur, prev
    return data


def _resolution(cur, prev, name, rows, in_cur, in_prev):
    """Median hours from when an item arrived (first column) to the first of
    the times it was resolved (other columns), per period of resolution."""
    done = []
    for row in rows:
        arrived = row[0]
        times = [when for when in row[1:] if when is not None and when >= arrived]
        if times:
            done.append((arrived, min(times)))
    for figures_, check in ((cur, in_cur), (prev, in_prev)):
        waits = [_hours(resolved - arrived) for arrived, resolved in done if check(resolved)]
        figures_[name] = _median(waits)
        figures_[name + '_count'] = len(waits)


# -- presentation (labels in the visitor's language) ----------------------------

def format_hours(value):
    if value is None:
        return '–'
    if value < 1:
        return _('%d min') % max(1, round(value * 60))
    if value < 48:
        return (_('%.0f h') if value >= 10 else _('%.1f h')) % value
    return _('%.1f d') % (value / 24)


def percent(rate):
    return round(100.0 * rate['num'] / rate['den']) if rate and rate['den'] else None


def _value(kind, raw):
    if kind == 'rate':
        return percent(raw)
    return raw


def _text(kind, value):
    if value is None:
        return '–'
    if kind == 'rate':
        return '%d%%' % value
    if kind == 'hours':
        return format_hours(value)
    return formats.number_format(value, force_grouping=True)


def _trend(kind, cur, prev, better):
    """{'arrow', 'text', 'words', 'tone'}: the change from the previous period.
    tone is 'good', 'bad' or '' (no change, or a figure that is neither)."""
    if cur is None or prev is None:
        return None
    if kind == 'rate':
        change = cur - prev
        text = ngettext('%(sign)s%(n)d point', '%(sign)s%(n)d points', abs(change)) % {
            'sign': '+' if change > 0 else '−' if change < 0 else '', 'n': abs(change)}
    elif prev == 0:
        if cur == 0:
            change, text = 0, ''
        else:
            change, text = cur, _('new')
    else:
        change = cur - prev
        pct = round(100.0 * abs(change) / prev)
        if pct == 0 and change:
            change = 0
        text = '%s%d%%' % ('+' if change > 0 else '−' if change < 0 else '', pct)
    if change > 0:
        arrow, words = '▲', _('up')
    elif change < 0:
        arrow, words = '▼', _('down')
    else:
        arrow, words, text = '•', _('no change'), ''
    tone = ''
    if change and better:
        tone = 'good' if (change > 0) == (better == 'up') else 'bad'
    return {'arrow': arrow, 'text': text, 'words': words, 'tone': tone}


def tiles(data, inbox_counts=None):
    """The tiles, in groups: [{'title', 'tiles': [{'key', 'label', 'value', 'previous',
    'trend', 'note', 'url'}]}]."""
    from django.urls import reverse
    cur, prev = data['cur'], data['prev']
    inbox_counts = inbox_counts or {}

    def tile(key, label, kind='count', better='up', note='', url=None, trend=True):
        c, p = _value(kind, cur.get(key)), _value(kind, prev.get(key))
        return {'key': key, 'label': label, 'value': _text(kind, c), 'previous': _text(kind, p) if trend else None,
                'trend': _trend(kind, c, p, better) if trend else None, 'note': note, 'url': url}

    week_note = _('needs questions at least 7 days old') if cur['answered_7d']['den'] == 0 else ''
    member_note = _('needs members who joined at least 7 days ago') if cur['posted_7d']['den'] == 0 else ''
    waiting = tile('waiting', _('Waiting in the moderation inbox'), better='down', trend=False,
                   url=reverse('admin_moderation'))
    waiting['value'] = _text('count', inbox_counts.get('total', 0))
    waiting['note'] = ', '.join(text for count, text in (
        (inbox_counts.get('review'), _('%d first posts') % (inbox_counts.get('review') or 0)),
        (inbox_counts.get('flags'), _('%d flagged posts') % (inbox_counts.get('flags') or 0)),
        (inbox_counts.get('edits'), _('%d suggested edits') % (inbox_counts.get('edits') or 0)),
        (inbox_counts.get('users'), _('%d reported members') % (inbox_counts.get('users') or 0)),
        (inbox_counts.get('ai'), _('%d automated reports') % (inbox_counts.get('ai') or 0)),
    ) if count)
    moderation = [
        waiting,
        tile('flag_resolution', _('Median time to resolve a flag'), 'hours', better='down',
             note=ngettext('%d flag resolved', '%d flags resolved', cur['flag_resolution_count'])
             % cur['flag_resolution_count']),
        tile('review_resolution', _('Median time to review a first post'), 'hours', better='down',
             note=ngettext('%d post reviewed', '%d posts reviewed', cur['review_resolution_count'])
             % cur['review_resolution_count']),
        tile('edit_resolution', _('Median time to review a suggested edit'), 'hours', better='down',
             note=ngettext('%d edit reviewed', '%d edits reviewed', cur['edits_reviewed']) % cur['edits_reviewed']),
        tile('suspensions', _('Accounts suspended'), better=None),
    ]
    if cur['ai_reports'] or prev['ai_reports'] or data.get('ai_reports_open'):
        moderation.append(tile('ai_reports', _('Automated (AI) reports'), better=None,
                               note=_('%d still open') % data.get('ai_reports_open', 0)))
    return [
        {'id': 'activity', 'title': _('Activity'), 'tiles': [
            tile('questions', _('New questions')),
            tile('answers', _('New answers')),
            tile('comments', _('New comments')),
        ]},
        {'id': 'answering', 'title': _('Answering'), 'tiles': [
            tile('answered_24h', _('Questions answered within 24 hours'), 'rate'),
            tile('answered_7d', _('Questions answered within 7 days'), 'rate', note=week_note),
            tile('median_first_answer', _('Median time to first answer'), 'hours', better='down'),
            tile('accepted', _('Questions with an accepted answer'), 'rate'),
            tile('backlog', _('Unanswered backlog'), better='down', url=reverse('admin_support')),
        ]},
        {'id': 'members', 'title': _('Members'), 'tiles': [
            tile('members', _('New members')),
            tile('confirmed_email', _('New members who confirmed their email'), 'rate'),
            tile('posted_7d', _('New members who posted within 7 days'), 'rate', note=member_note),
            tile('active_members', _('Active members (posted or voted)')),
            tile('returning_members', _('Returning members')),
        ]},
        {'id': 'moderation', 'title': _('Moderation'), 'tiles': moderation},
    ]


def bucket_labels():
    return {'1h': _('under 1 hour'), '3h': _('1 to 3 hours'), '6h': _('3 to 6 hours'), '24h': _('6 to 24 hours'),
            '3d': _('1 to 3 days'), '7d': _('3 to 7 days'), 'more': _('over 7 days'), 'none': _('no answer yet')}


def chart_data(data):
    """What the page's charts and tables show, with labels."""
    import time
    daily = data['daily']
    labels = bucket_labels()
    largest = max([count for key, count in data['buckets']] or [0])
    buckets = [{'key': key, 'label': labels[key], 'count': count,
                'width': round(100.0 * count / largest) if largest else 0} for key, count in data['buckets']]
    most = max([row['cur'] for row in data['tags']] + [row['prev'] for row in data['tags']] + [0])
    tags = [dict(row, cur_width=round(100.0 * row['cur'] / most) if most else 0,
                 prev_width=round(100.0 * row['prev'] / most) if most else 0) for row in data['tags']]
    peak = max([n for day in data['heatmap'] for n in day] or [0])
    heatmap = []
    for weekday, hours in enumerate(data['heatmap']):
        heatmap.append({'name': WEEKDAYS[weekday], 'hours': [
            {'hour': hour, 'count': n, 'level': math.ceil(4.0 * n / peak) if peak else 0}
            for hour, n in enumerate(hours)]})

    def series(field):
        return [[time.mktime(row['day'].timetuple()) * 1000, row[field]] for row in daily]

    return {
        'series': [
            {'label': _('Questions'), 'data': series('questions')},
            {'label': _('Answers'), 'data': series('answers')},
            {'label': _('New members'), 'data': series('members')},
        ],
        'buckets': buckets,
        'tags': tags,
        'heatmap': heatmap,
        'answerers': _answerers(data['answerers']),
        'summaries': summaries(data),
    }


def _answerers(rows):
    from forum.models import User
    result = []
    for row in rows:
        user = User(id=row['id'], username=row['username'])
        result.append(dict(row, url=user.get_profile_url()))
    return result


def summaries(data):
    """One plain sentence per chart (also read by screen readers)."""
    cur, days = data['cur'], data['days']
    out = {}
    daily = data['daily']
    busiest = max(daily, key=lambda row: (row['questions'] + row['answers'], row['day'])) if daily else None
    text = _('In the last %(days)d days: %(questions)d questions, %(answers)d answers and %(members)d new members.') % {
        'days': days, 'questions': cur['questions'], 'answers': cur['answers'], 'members': cur['members']}
    if busiest and busiest['questions'] + busiest['answers']:
        text += ' ' + _('Busiest day: %(day)s (%(questions)d questions, %(answers)d answers).') % {
            'day': formats.date_format(busiest['day'], 'MONTH_DAY_FORMAT'),
            'questions': busiest['questions'], 'answers': busiest['answers']}
    out['daily'] = text

    counts = dict(data['buckets'])
    answered = sum(n for key, n in data['buckets'] if key != 'none')
    if not cur['questions']:
        out['buckets'] = _('No questions were asked in this period.')
    else:
        fast = counts['1h'] + counts['3h'] + counts['6h']
        out['buckets'] = _('Of %(total)d questions, %(answered)d have an answer: %(fast)d within 6 hours, '
                           '%(day)d within a day. %(none)d have no answer yet.') % {
            'total': cur['questions'], 'answered': answered, 'fast': fast, 'day': fast + counts['24h'],
            'none': counts['none']}

    if data['tags']:
        top = data['tags'][0]
        out['tags'] = _('Fastest growing tag: %(name)s, %(cur)d questions (%(prev)d in the previous period).') % top
    else:
        out['tags'] = _('No tag was used more than in the previous period.')

    if data['answerers']:
        top = data['answerers'][0]
        out['answerers'] = ngettext(
            'Top answerer: %(username)s with %(answers)d answer (%(accepted)d accepted).',
            'Top answerer: %(username)s with %(answers)d answers (%(accepted)d accepted).', top['answers']) % top
    else:
        out['answerers'] = _('Nobody answered a question in this period.')

    peak, where = 0, None
    for weekday, hours in enumerate(data['heatmap']):
        for hour, n in enumerate(hours):
            if n > peak:
                peak, where = n, (weekday, hour)
    if where is None:
        out['heatmap'] = _('No questions were asked in this period.')
    else:
        out['heatmap'] = ngettext(
            'Busiest hour: %(weekday)s %(hour)02d:00 to %(next)02d:00, with %(n)d question.',
            'Busiest hour: %(weekday)s %(hour)02d:00 to %(next)02d:00, with %(n)d questions.', peak) % {
            'weekday': WEEKDAYS[where[0]], 'hour': where[1], 'next': (where[1] + 1) % 24, 'n': peak}
    return out


def headline(groups, data):
    """The tiles in one sentence, for the top of the page."""
    found = {t['key']: t for group in groups for t in group['tiles']}
    return _('Last %(days)d days: %(questions)s new questions, %(answered)s answered within 24 hours, '
             'median first answer %(median)s, %(members)s new members, %(waiting)s items waiting for moderators.') % {
        'days': data['days'], 'questions': found['questions']['value'], 'answered': found['answered_24h']['value'],
        'median': found['median_first_answer']['value'], 'members': found['members']['value'],
        'waiting': found['waiting']['value']}


def _cell(text):
    """A name as a CSV cell that spreadsheets won't run as a formula."""
    text = str(text)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def write_csv(out, data, groups, charts):
    """The figures as CSV (section, item, value for the period, previous period)."""
    writer = csv.writer(out)
    writer.writerow([_('Section'), _('Figure'), _('Last %d days') % data['days'], _('Previous %d days') % data['days']])
    for group in groups:
        for t in group['tiles']:
            writer.writerow([group['title'], t['label'], t['value'], t['previous'] if t['previous'] is not None else ''])
    writer.writerow([])
    writer.writerow([_('Day'), _('Questions'), _('Answers'), _('Comments'), _('New members')])
    for row in data['daily']:
        writer.writerow([row['day'].isoformat(), row['questions'], row['answers'], row['comments'], row['members']])
    writer.writerow([])
    writer.writerow([_('Time to first answer'), _('Questions')])
    for bucket in charts['buckets']:
        writer.writerow([bucket['label'], bucket['count']])
    writer.writerow([])
    writer.writerow([_('Tag'), _('Questions'), _('Previous period')])
    for row in data['tags']:
        writer.writerow([_cell(row['name']), row['cur'], row['prev']])
    writer.writerow([])
    writer.writerow([_('Member'), _('Answers'), _('Accepted')])
    for row in data['answerers']:
        writer.writerow([_cell(row['username']), row['answers'], row['accepted']])
    writer.writerow([])
    writer.writerow([_('Day of the week')] + ['%02d' % hour for hour in range(24)])
    for weekday, hours in enumerate(data['heatmap']):
        writer.writerow([str(WEEKDAYS[weekday])] + hours)
