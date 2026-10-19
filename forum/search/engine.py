"""Question search: a parsed query turned into a queryset of questions.

The site search page, the MCP search_forum tool and the AI module (through
forum.utils.similar.search_questions) all use it.
"""
from django.db.models import Case, Count, IntegerField, Q, Value, When

from forum.utils.review import hidden_q

from . import backends
from .filters import CLOSED_MARK, status_q
from .query import parse

# Facets (status and top tags) are counted over at most this many matches.
FACET_CAP = 1000
TOP_TAGS = 8


def index_ready():
    """False until rebuild_search_index has run on a forum that has questions
    (or the migration created an empty index on a new one)."""
    from forum.models import Node, SearchDocument
    return SearchDocument.objects.exists() or not Node.objects.filter(node_type='question').exists()


def apply_filters(questions, query):
    """Tag, user and is: filters of `query` on a Question queryset."""
    from forum.models import Tag, TagSynonym
    for name in query.tags:
        name = TagSynonym.resolve([name])[0]
        tag = Tag.objects.filter(name__iexact=name).values_list('id', flat=True).first()
        questions = questions.filter(tags__id=tag) if tag else questions.none()
    for name in query.users:
        questions = questions.filter(author__username__iexact=name)
    for flag in query.flags:
        questions = questions.filter(status_q(flag))
    return questions


def _legacy_ids(query, limit):
    """Before the index is built: title/body/answer LIKE matching on the posts."""
    from forum.models import Question
    from django.db.models import Q
    match = Q()
    for word in query.terms + query.phrases:
        match |= Q(title__icontains=word) | Q(body__icontains=word)
    questions = Question.objects.public().filter(match)
    for word in query.excludes:
        questions = questions.exclude(Q(title__icontains=word) | Q(body__icontains=word))
    return list(questions.order_by('-added_at').values_list('id', flat=True)[:limit])


def search(query, limit=backends.MAX_RESULTS, filters=None, skip=()):
    """(questions, ranked): the questions matching `query` (a string or a
    ParsedQuery) and `filters` (forum.search.filters.SearchFilters, except
    those named in `skip`), not deleted nor held for review. When `ranked`,
    they are annotated with search_rank (0 is the best match) for ordering
    by relevance."""
    from forum.models import Question
    if isinstance(query, str):
        query = parse(query)
    questions = apply_filters(Question.objects.public(), query)
    if filters is not None:
        questions = filters.apply(questions, skip=skip)
    if '_index_ready' not in query.__dict__:
        query._index_ready = index_ready()
    ready = query._index_ready

    if query.has_text:
        # Kept on the query: the facets search again with fewer filters.
        memo = query.__dict__.setdefault('_ranked_ids', {})
        if (ready, limit) not in memo:
            memo[ready, limit] = (backends.rank(query.terms, query.phrases, query.excludes, limit) if ready
                                  else _legacy_ids(query, limit))
        ids = memo[ready, limit]
        questions = questions.filter(id__in=ids).annotate(search_rank=Case(
            *[When(id=qid, then=Value(position)) for position, qid in enumerate(ids)],
            default=Value(len(ids)), output_field=IntegerField()))
        return questions, True

    if query.excludes:
        if ready:
            excluded = backends.rank(query.excludes, [], [], limit=100000)
            questions = questions.exclude(id__in=excluded)
        else:
            from django.db.models import Q
            for word in query.excludes:
                questions = questions.exclude(Q(title__icontains=word) | Q(body__icontains=word))
    return questions, False


def question_provider(text, limit=10, filters=None):
    """A forum.utils.similar search provider: the best matching questions."""
    query = parse(text)
    if not query.has_text or not index_ready():
        return []
    pool = max(limit * 3, 30) if filters is None or filters.is_empty else backends.MAX_RESULTS
    questions, ranked = search(query, limit=pool, filters=filters)
    return list(questions.order_by('search_rank')[:limit])


question_provider.accepts_filters = True


# -- facets ------------------------------------------------------------------

def matched_rows(questions, cap=FACET_CAP):
    """[(id, state_string)] of up to `cap` of the questions (the newest)."""
    return list(questions.order_by('-id').values_list('id', 'state_string')[:cap])


def status_facets(rows):
    """{'total', 'unanswered', 'answered', 'accepted', 'closed', 'capped'}
    for (id, state_string) rows: one query on their answers."""
    from forum.models import Node
    ids = [row[0] for row in rows]
    counts = {'answered': 0, 'accepted': 0}
    if ids:
        counts = Node.objects.filter(parent_id__in=ids, node_type='answer').exclude(hidden_q()).aggregate(
            answered=Count('parent_id', distinct=True),
            accepted=Count('parent_id', distinct=True, filter=Q(marked=True)))
    return {'total': len(ids), 'answered': counts['answered'] or 0, 'accepted': counts['accepted'] or 0,
            'unanswered': len(ids) - (counts['answered'] or 0),
            'closed': sum(1 for row in rows if CLOSED_MARK in (row[1] or '')), 'capped': len(ids) >= FACET_CAP}


def tag_facets(ids, leave_out=(), limit=TOP_TAGS):
    """[(tag name, count)] of the commonest tags of the questions `ids`."""
    from forum.models import Node
    if not ids:
        return []
    through = Node.tags.through
    rows = through.objects.filter(node_id__in=ids).exclude(tag__name__in=list(leave_out)).values(
        'tag__name').annotate(n=Count('node_id')).order_by('-n', 'tag__name')[:limit]
    return [(row['tag__name'], row['n']) for row in rows]
