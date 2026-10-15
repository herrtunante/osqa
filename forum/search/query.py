"""Parsing of the search box: words, "quoted phrases", -exclusions and
operators ([tag], tag:name, is:answered, is:unanswered, is:accepted, user:name).
"""
import re

from forum.utils.similar import STOPWORDS

# One token: an optional minus, then a quoted string (possibly unterminated),
# an operator with a quoted value, or a run of non-space characters.
TOKEN = re.compile(r'(-?)"([^"]*)"?|(\w+):"([^"]*)"?|(\S+)', re.UNICODE)
WORD = re.compile(r'\w+', re.UNICODE)
TAG_BRACKETS = re.compile(r'^\[([^\]\s]+)\]$')
IS_FLAGS = ('answered', 'unanswered', 'accepted')

# Words kept per kind, so a pasted page of text can't build a huge query.
MAX_ITEMS = 12


def words(text):
    return [w.lower() for w in WORD.findall(text)]


class ParsedQuery:
    def __init__(self, raw=''):
        self.raw = raw or ''
        self.terms = []      # words, any of which may match (ranked)
        self.phrases = []    # each must appear
        self.excludes = []   # words or phrases that must not appear
        self.tags = []
        self.users = []
        self.flags = []      # 'answered', 'unanswered', 'accepted'
        self.free_text = []  # the words and phrases as typed, for the ask page title

    @property
    def has_text(self):
        """True when there is something to rank by (words or phrases)."""
        return bool(self.terms or self.phrases)

    @property
    def has_filters(self):
        return bool(self.tags or self.users or self.flags or self.excludes)

    @property
    def is_empty(self):
        return not (self.has_text or self.has_filters)

    @property
    def title_text(self):
        return ' '.join(self.free_text)[:300]

    def highlight_terms(self):
        """Words and phrases (as word lists) to mark in snippets."""
        return [[t] for t in self.terms] + [words(p) for p in self.phrases]

    def __repr__(self):
        return '<ParsedQuery terms=%r phrases=%r excludes=%r tags=%r users=%r flags=%r>' % (
            self.terms, self.phrases, self.excludes, self.tags, self.users, self.flags)


def _add(items, value):
    if value and value not in items and len(items) < MAX_ITEMS:
        items.append(value)


def parse(raw):
    query = ParsedQuery(raw)
    plain = []
    for match in TOKEN.finditer((raw or '')[:1000]):
        minus, quoted, op, op_value, bare = match.groups()
        if quoted is not None:
            phrase = ' '.join(words(quoted))
            if not phrase:
                continue
            if minus:
                _add(query.excludes, phrase)
            elif ' ' in phrase:
                _add(query.phrases, phrase)
                query.free_text.append(quoted.strip())
            else:
                plain.append(phrase)
                query.free_text.append(quoted.strip())
            continue
        if op is not None:
            _operator(query, op.lower(), op_value.strip(), plain)
            continue
        tag = TAG_BRACKETS.match(bare)
        if tag:
            _add(query.tags, tag.group(1).lower())
        elif ':' in bare and not bare.startswith(':') and _operator(query, *bare.lower().split(':', 1), plain=None):
            pass
        elif bare.startswith('-') and len(bare) > 1:
            for word in words(bare[1:]):
                _add(query.excludes, word)
        else:
            plain.extend(words(bare))
            query.free_text.append(bare)

    significant = [w for w in plain if w not in STOPWORDS]
    for word in significant or plain:
        _add(query.terms, word)
    return query


def _operator(query, op, value, plain):
    """Applies `op:value`; False when op isn't an operator (then it is text)."""
    if not value:
        return op in ('tag', 'user', 'is')
    if op == 'tag':
        _add(query.tags, value.lower().strip('[]'))
    elif op == 'user':
        _add(query.users, value)
    elif op == 'is' and value in IS_FLAGS:
        _add(query.flags, value)
    elif plain is not None:
        plain.extend(words(value))
        query.free_text.append(value)
    else:
        return False
    return True
