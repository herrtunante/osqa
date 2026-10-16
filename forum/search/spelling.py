"""'Did you mean' for searches with few or no results: each word of the
query that the forum doesn't use is replaced by the closest tag name or
frequent title word, and [tag] operators naming no tag by the closest tag.

The vocabulary (tag names and synonyms, title words with their frequency)
is built from a query each on tags, synonyms and recent titles, and kept in
the shared cache for an hour.
"""
import collections
import difflib
import re

from django.core.cache import cache

from forum.utils.similar import STOPWORDS

from .query import words
from .text import stem

VOCABULARY_KEY = 'search-vocabulary-v1'
VOCABULARY_SECONDS = 3600
MAX_TAGS = 3000
MAX_TITLES = 5000
MIN_TITLE_COUNT = 2
CUTOFF = 0.75


def vocabulary():
    """{'words': {word: frequency}, 'tags': [tag names]}, cached."""
    data = cache.get(VOCABULARY_KEY)
    if data is not None:
        return data
    from forum.models import Question, Tag, TagSynonym
    counts = collections.Counter()
    tags = []
    for name, used in Tag.active.filter(used_count__gt=0).order_by('-used_count').values_list(
            'name', 'used_count')[:MAX_TAGS]:
        tags.append(name)
        for word in words(name):
            counts[word] += used
    titles = collections.Counter()
    for title in Question.objects.public().order_by('-last_activity_at').values_list('title', flat=True)[:MAX_TITLES]:
        titles.update(set(words(title or '')))
    for word, n in titles.items():
        if n >= MIN_TITLE_COUNT or word in counts:
            counts[word] += n
    data = {'words': {w: n for w, n in counts.items() if len(w) >= 3 and w not in STOPWORDS}, 'tags': tags,
            'synonyms': list(TagSynonym.objects.values_list('source_name', flat=True)[:MAX_TAGS])}
    cache.set(VOCABULARY_KEY, data, VOCABULARY_SECONDS)
    return data


def _closest(word, candidates, frequency=None):
    pool = [c for c in candidates if abs(len(c) - len(word)) <= 2]
    close = difflib.get_close_matches(word, pool, n=5, cutoff=CUTOFF)
    if not close:
        return None
    if frequency:
        # Among the closest, the most used (difflib already sorted them by similarity).
        best = difflib.SequenceMatcher(None, word, close[0]).ratio()
        close = [c for c in close if difflib.SequenceMatcher(None, word, c).ratio() >= best - 0.08]
        close.sort(key=lambda c: -frequency.get(c, 0))
    return close[0]


def _replace(text, old, new):
    return re.sub(r'(?<![\w:\[-])%s(?!\w)' % re.escape(old), new.replace('\\', '\\\\'), text, count=1,
                  flags=re.IGNORECASE | re.UNICODE)


def suggest(query):
    """The query text with misspelled words corrected, or None."""
    data = vocabulary()
    known = data['words']
    if not known and not data['tags']:
        return None
    text = (query.raw or '')[:1000]
    changed = False
    # Operators first, by position from the end (the spans are in the raw text).
    tags = set(data['tags']) | set(data.get('synonyms', ()))
    for kind, value, start, end in sorted(query.operators, key=lambda op: -op[2]):
        if kind == 'tag' and value not in tags:
            better = _closest(value, data['tags'])
            if better:
                text = text[:start] + '[%s]' % better + text[end:]
                changed = True
    for term in query.terms + [w for phrase in query.phrases for w in words(phrase)]:
        if len(term) < 3 or term.isdigit() or term in known or stem(term) in known:
            continue
        better = _closest(term, known, known)
        if better and better != term:
            replaced = _replace(text, term, better)
            changed = changed or replaced != text
            text = replaced
    return text if changed else None
