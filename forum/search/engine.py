"""Question search: a parsed query turned into a queryset of questions.

The site search page, the MCP search_forum tool and the AI module (through
forum.utils.similar.search_questions) all use it.
"""
from django.db.models import Case, Exists, IntegerField, OuterRef, Value, When

from . import backends
from .query import parse


def index_ready():
    """False until rebuild_search_index has run on a forum that has questions
    (or the migration created an empty index on a new one)."""
    from forum.models import Node, SearchDocument
    return SearchDocument.objects.exists() or not Node.objects.filter(node_type='question').exists()


def _answers(**kwargs):
    from forum.models import Node
    return Node.objects.filter(parent=OuterRef('pk'), node_type='answer', **kwargs).exclude(
        state_string__contains='(deleted)')


def apply_filters(questions, query):
    """Tag, user and is: filters of `query` on a Question queryset."""
    from forum.models import Tag, TagSynonym
    for name in query.tags:
        name = TagSynonym.resolve([name])[0]
        tag = Tag.objects.filter(name__iexact=name).values_list('id', flat=True).first()
        questions = questions.filter(tags__id=tag) if tag else questions.none()
    for name in query.users:
        questions = questions.filter(author__username__iexact=name)
    if 'answered' in query.flags:
        questions = questions.filter(Exists(_answers()))
    if 'unanswered' in query.flags:
        questions = questions.filter(~Exists(_answers()))
    if 'accepted' in query.flags:
        questions = questions.filter(Exists(_answers(marked=True)))
    return questions


def _legacy_ids(query, limit):
    """Before the index is built: title/body/answer LIKE matching on the posts."""
    from forum.models import Question
    from django.db.models import Q
    match = Q()
    for word in query.terms + query.phrases:
        match |= Q(title__icontains=word) | Q(body__icontains=word)
    questions = Question.objects.filter_state(deleted=False).filter(match)
    for word in query.excludes:
        questions = questions.exclude(Q(title__icontains=word) | Q(body__icontains=word))
    return list(questions.order_by('-added_at').values_list('id', flat=True)[:limit])


def search(query, limit=backends.MAX_RESULTS):
    """(questions, ranked): the questions matching `query` (a string or a
    ParsedQuery), not deleted. When `ranked`, they are annotated with
    search_rank (0 is the best match) for ordering by relevance."""
    from forum.models import Question
    if isinstance(query, str):
        query = parse(query)
    questions = apply_filters(Question.objects.filter_state(deleted=False), query)
    ready = index_ready()

    if query.has_text:
        if ready:
            ids = backends.rank(query.terms, query.phrases, query.excludes, limit)
        else:
            ids = _legacy_ids(query, limit)
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


def question_provider(text, limit=10):
    """A forum.utils.similar search provider: the best matching questions."""
    query = parse(text)
    if not query.has_text or not index_ready():
        return []
    questions, ranked = search(query, limit=max(limit * 3, 30))
    return list(questions.order_by('search_rank')[:limit])
