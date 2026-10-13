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
    pool = Question.objects.filter_state(deleted=False).filter(match).order_by('-added_at')[:300]

    def score(question):
        title, tags = question.title.lower(), question.tagnames.lower()
        return sum(2 * (word in title) + (word in tags) for word in words)

    ranked = sorted(((score(q), q) for q in pool), key=lambda pair: -pair[0])
    return [q for s, q in ranked if s > 0][:limit]
