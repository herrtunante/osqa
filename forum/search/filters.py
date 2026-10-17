"""Search filters kept in the URL of the search page (and taken by the MCP
search_forum tool): they narrow the questions a query matches, together
with the query's own operators ([tag], user:, is:).

    tags=a b      every tag (synonyms resolved; several params or spaces)
    status=       unanswered | answered | accepted (an accepted answer)
    closed=hide   leave out closed questions
    date=         week | month | year | custom (with from=/to= as YYYY-MM-DD)
    author=name   asked by this member
    code=1        the question or one of its answers has a code block
    min_score=n   a score of at least n
    version=4.1   the question or one of its answers applies to this version
                  (forum/utils/post_versions.py; of the filter's tags, if any)

Everything is applied in SQL on a Question queryset (apply()); answers held
for review or deleted don't count (forum/utils/review.py).
"""
import datetime
import re

from django.db.models import Exists, OuterRef, Q

from forum.utils.review import hidden_q

STATUSES = ('unanswered', 'answered', 'accepted')
DATE_DAYS = {'week': 7, 'month': 30, 'year': 365}
DATES = tuple(DATE_DAYS) + ('custom',)
MAX_TAGS = 5
MAX_SCORE = 10 ** 6
CLOSED_MARK = '(closed)'
CODE_MARK = '<pre'

# The URL parameters, in the order they are written.
PARAMS = ('tags', 'status', 'closed', 'date', 'from', 'to', 'author', 'code', 'min_score', 'version')
MAX_VERSION_LENGTH = 20


def _answers(**kwargs):
    from forum.models import Node
    return Node.objects.filter(parent=OuterRef('pk'), node_type='answer', **kwargs).exclude(hidden_q())


def status_q(status):
    """Q for questions that are unanswered, answered or have an accepted answer."""
    if status == 'unanswered':
        return ~Exists(_answers())
    if status == 'answered':
        return Exists(_answers())
    if status == 'accepted':
        return Exists(_answers(marked=True))
    return Q()


def version_q(name, tag_ids=None):
    """Q for questions whose own version, or a visible answer's, is `name`
    (or was confirmed for it), optionally of the tags `tag_ids` only."""
    from forum.models import Node, NodeVersion
    versions = NodeVersion.objects.filter(Q(version__name__iexact=name) | Q(confirmed_version__name__iexact=name))
    if tag_ids:
        versions = versions.filter(tag_id__in=tag_ids)
    answers = Node.objects.filter(node_type='answer').exclude(hidden_q()).values('id')
    return Exists(versions.filter(node_id=OuterRef('pk'))) | Exists(
        versions.filter(node__parent_id=OuterRef('pk'), node_id__in=answers))


def parse_date(value):
    """A date from YYYY-MM-DD (or the start of an ISO date and time), or None."""
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def tag_names(values):
    """Tag names from `values` (strings of names separated by spaces or
    commas, or [brackets]), lower case, without repeats."""
    names = []
    for value in values:
        for name in re.split(r'[\s,]+', str(value or '')):
            name = name.strip('[]').lower()[:255]
            if name and name not in names and len(names) < MAX_TAGS:
                names.append(name)
    return names


class SearchFilters:
    def __init__(self, tags=(), status='', hide_closed=False, date='', date_from=None, date_to=None, author='',
                 has_code=False, min_score=None, version=''):
        self.tags = tag_names(tags)
        self.status = status if status in STATUSES else ''
        self.hide_closed = bool(hide_closed)
        self.date_from, self.date_to = date_from, date_to
        if date not in DATES:
            date = 'custom' if (date_from or date_to) else ''
        if date != 'custom':
            self.date_from = self.date_to = None
        elif not (date_from or date_to):
            date = ''
        self.date = date
        self.author = (author or '').strip()[:30]
        self.has_code = bool(has_code)
        self.min_score = min_score
        self.version = ' '.join(str(version or '').split())[:MAX_VERSION_LENGTH]

    @classmethod
    def from_params(cls, params):
        """From a QueryDict (request.GET) or a plain dict; bad values are ignored."""
        def getlist(key):
            if hasattr(params, 'getlist'):
                return params.getlist(key)
            value = params.get(key)
            if value in (None, ''):
                return []
            return list(value) if isinstance(value, (list, tuple)) else [value]

        def get(key):
            return (getlist(key) or [''])[-1]

        try:
            min_score = max(-MAX_SCORE, min(MAX_SCORE, int(str(get('min_score')).strip())))
        except ValueError:
            min_score = None
        return cls(tags=getlist('tags'), status=str(get('status')).strip().lower(),
                   hide_closed=str(get('closed')).lower() in ('hide', '1', 'true', 'on'),
                   date=str(get('date')).strip().lower(), date_from=parse_date(get('from')), date_to=parse_date(get('to')),
                   author=str(get('author')), has_code=str(get('code')).lower() in ('1', 'true', 'on', 'yes'),
                   min_score=min_score, version=str(get('version')))

    def params(self):
        """[(name, value)] for a URL, only the filters that are set."""
        out = []
        if self.tags:
            out.append(('tags', ' '.join(self.tags)))
        if self.status:
            out.append(('status', self.status))
        if self.hide_closed:
            out.append(('closed', 'hide'))
        if self.date:
            out.append(('date', self.date))
        if self.date_from:
            out.append(('from', self.date_from.isoformat()))
        if self.date_to:
            out.append(('to', self.date_to.isoformat()))
        if self.author:
            out.append(('author', self.author))
        if self.has_code:
            out.append(('code', '1'))
        if self.min_score is not None:
            out.append(('min_score', str(self.min_score)))
        if self.version:
            out.append(('version', self.version))
        return out

    def copy(self, **changes):
        values = dict(tags=self.tags, status=self.status, hide_closed=self.hide_closed, date=self.date,
                      date_from=self.date_from, date_to=self.date_to, author=self.author, has_code=self.has_code,
                      min_score=self.min_score, version=self.version)
        values.update(changes)
        return SearchFilters(**values)

    def without(self, name, value=None):
        """A copy without filter `name` (one tag when `value` is given)."""
        if name == 'tags':
            return self.copy(tags=[t for t in self.tags if value is not None and t != value])
        changes = {'status': {'status': ''}, 'closed': {'hide_closed': False},
                   'date': {'date': '', 'date_from': None, 'date_to': None}, 'author': {'author': ''},
                   'code': {'has_code': False}, 'min_score': {'min_score': None}, 'version': {'version': ''}}[name]
        return self.copy(**changes)

    @property
    def active_count(self):
        return len(self.tags) + sum(1 for on in (self.status, self.hide_closed, self.date, self.author, self.has_code,
                                                 self.min_score is not None, self.version) if on)

    @property
    def is_empty(self):
        return not self.active_count

    def since(self, now=None):
        """The first day included by the date filter, or None."""
        if self.date in DATE_DAYS:
            now = now or datetime.datetime.now()
            return now - datetime.timedelta(days=DATE_DAYS[self.date])
        if self.date_from:
            return datetime.datetime.combine(self.date_from, datetime.time.min)
        return None

    def tag_ids(self):
        """The ids of the filter's tags (synonyms resolved), None if one
        doesn't exist; looked up once."""
        if '_tag_ids' not in self.__dict__:
            from forum.models import Tag, TagSynonym
            names = TagSynonym.resolve(self.tags)
            found = dict((name.lower(), tag_id) for tag_id, name in Tag.objects.filter(
                name__in=names).values_list('id', 'name'))
            for name in names:
                if name not in found:
                    # Typed in another case than the tag's ("Python"): rare.
                    tag_id = Tag.objects.filter(name__iexact=name).values_list('id', flat=True).first()
                    if tag_id is None:
                        found = None
                        break
                    found[name] = tag_id
            self._tag_ids = None if found is None else [found[name] for name in names]
        return self._tag_ids

    def apply(self, questions, skip=()):
        """`questions` narrowed by every filter, except those named in `skip`."""
        if self.tags and 'tags' not in skip:
            tag_ids = self.tag_ids()
            if tag_ids is None:
                return questions.none()
            for tag_id in tag_ids:
                questions = questions.filter(tags__id=tag_id)
        if self.status and 'status' not in skip:
            questions = questions.filter(status_q(self.status))
        if self.hide_closed and 'closed' not in skip:
            questions = questions.exclude(state_string__contains=CLOSED_MARK)
        if self.date and 'date' not in skip:
            since = self.since()
            if since:
                questions = questions.filter(added_at__gte=since)
            if self.date_to:
                questions = questions.filter(
                    added_at__lt=datetime.datetime.combine(self.date_to + datetime.timedelta(days=1), datetime.time.min))
        if self.author and 'author' not in skip:
            questions = questions.filter(author__username__iexact=self.author)
        if self.has_code and 'code' not in skip:
            questions = questions.filter(Q(body__contains=CODE_MARK) | Exists(_answers(body__contains=CODE_MARK)))
        if self.min_score is not None and 'min_score' not in skip:
            questions = questions.filter(score__gte=self.min_score)
        if self.version and 'version' not in skip:
            tag_ids = self.tag_ids() if self.tags else None
            questions = questions.filter(version_q(self.version, tag_ids))
        return questions

    def restrict(self, questions):
        """The questions of a list (another search provider's results) that
        pass the filters, in the same order: one query."""
        from forum.models import Question
        questions = list(questions)
        if self.is_empty or not questions:
            return questions
        keep = set(self.apply(Question.objects.public().filter(id__in=[q.id for q in questions])).values_list(
            'id', flat=True))
        return [q for q in questions if q.id in keep]

    def __repr__(self):
        return '<SearchFilters %r>' % self.params()
