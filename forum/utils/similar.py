"""Keyword matching of questions, for "related questions" without a full-text index."""
import re

from django.db.models import Q

from forum.models import Question

STOPWORDS = set("""
a about after all also am an and any are as at be because been before being between both but by can
cannot could did do does doing done each for from get gets getting had has have having how i if in into
is it its just me more most my no not of on one only or other our out over own same should so some such
than that the their them then there these they this those through to too under until up use using very
want was way we were what when where which while who why will with would you your
""".split())

WORD = re.compile(r"[^\W_][\w#+.-]*[\w#+]|[^\W_]", re.UNICODE)

def keywords(text, limit=8):
    """Distinct significant words of `text`, with plural/verb endings removed."""
    found = []
    for word in WORD.findall(text.lower()):
        if len(word) < 3 or word in STOPWORDS:
            continue
        for suffix in ('ing', 'ed', 'es', 's'):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[:-len(suffix)]
                break
        if word not in found:
            found.append(word)
    return found[:limit]


def candidate_questions(title, limit=10):
    """Questions whose title or tags share the most keywords with `title`."""
    words = keywords(title)
    if not words:
        return []

    match = Q()
    for word in words:
        match |= Q(title__icontains=word) | Q(tagnames__icontains=word)
    pool = Question.objects.public().filter(match).order_by('-added_at')[:300]

    def score(question):
        title, tags = question.title.lower(), question.tagnames.lower()
        return sum(2 * (word in title) + (word in tags) for word in words)

    ranked = sorted(((score(q), q) for q in pool), key=lambda pair: -pair[0])
    return [q for s, q in ranked if s > 0][:limit]


def full_text_questions(query, limit=10):
    """The core full-text index (forum.search), ranked."""
    from forum.search.engine import question_provider
    return question_provider(query, limit)


# Search engines register a function (query, limit) -> [Question] here: the
# core full-text index first, then e.g. the AI module's semantic index. Their
# results are blended (reciprocal rank fusion); keyword matching is the fallback.
SEARCH_PROVIDERS = [full_text_questions]

# Reciprocal rank fusion constant: a question ranked r by a provider scores 1 / (K + r).
RRF_K = 60


def search_questions(query, limit=10):
    lists = []
    for provider in SEARCH_PROVIDERS:
        try:
            results = provider(query, limit)
        except Exception:
            import logging
            logging.exception("Question search provider failed, using keyword matching")
            continue
        if results:
            lists.append(list(results))
    if not lists:
        return candidate_questions(query, limit)
    if len(lists) == 1:
        return lists[0][:limit]

    scores, questions, first_seen = {}, {}, {}
    for results in lists:
        for rank, question in enumerate(results):
            scores[question.id] = scores.get(question.id, 0) + 1.0 / (RRF_K + rank + 1)
            questions.setdefault(question.id, question)
            first_seen.setdefault(question.id, len(first_seen))
    order = sorted(scores, key=lambda qid: (-scores[qid], first_seen[qid]))
    return [questions[qid] for qid in order[:limit]]
