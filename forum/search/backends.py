"""Full-text matching of SearchDocument rows, one backend per database.

Each backend's rank(terms, phrases, excludes, limit) returns question ids,
best first. `terms` are words of which any may match (more matches, and
matches in the title, rank higher); every phrase must appear; no exclusion
may appear.

- SQLite: the FTS5 table forum_searchdocument_fts, ranked by bm25() with the
  title weighted highest. Without FTS5, keyword matching.
- MySQL: FULLTEXT indexes on the title and on all columns; natural language
  mode, boolean mode when there are phrases or exclusions.
- PostgreSQL: SearchVector/SearchRank (django.contrib.postgres), vectors
  computed at query time (no index: fine for small and medium forums).
- Anything else, or when a backend fails: keyword matching (LIKE) on the
  documents, scored in Python.
"""
import logging
import re

from django.db import connection, transaction

logger = logging.getLogger('forum.search')

MAX_RESULTS = 1000

# Relative weights of title, tags, question body and answers.
WEIGHTS = (10.0, 4.0, 2.0, 1.0)

_state = {}


def reset():
    _state.clear()


def sqlite_fts_available():
    if 'sqlite_fts' not in _state:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'forum_searchdocument_fts'")
                found = cursor.fetchone() is not None
            if found:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT rowid FROM forum_searchdocument_fts LIMIT 0")
            _state['sqlite_fts'] = found
        except Exception:
            _state['sqlite_fts'] = False
    return _state['sqlite_fts']


class KeywordBackend:
    """LIKE matching, for databases without a full-text index (and as fallback)."""
    name = 'keywords'
    POOL = 2000

    def _contains(self, value):
        from django.db.models import Q
        return Q(title__icontains=value) | Q(tagnames__icontains=value) | Q(body__icontains=value) | \
            Q(answers__icontains=value)

    def rank(self, terms, phrases, excludes, limit=MAX_RESULTS):
        from django.db.models import Q
        from forum.models import SearchDocument
        documents = SearchDocument.objects.all()
        if terms:
            match = Q()
            for term in terms:
                match |= self._contains(term)
            documents = documents.filter(match)
        for phrase in phrases:
            documents = documents.filter(self._contains(phrase))
        for exclude in excludes:
            documents = documents.exclude(self._contains(exclude))
        rows = documents.order_by('-question_id').values_list('question_id', 'title', 'tagnames', 'body',
                                                               'answers')[:self.POOL]
        needles = [re.compile(r'(?<!\w)%s' % re.escape(t), re.I | re.U) for t in list(terms) + list(phrases)]
        scored = []
        for row in rows:
            fields = [f or '' for f in row[1:]]
            score = sum(weight * min(len(n.findall(field)), 3)
                        for n in needles for weight, field in zip(WEIGHTS, fields))
            if needles and not score:
                continue
            scored.append((-score, -row[0], row[0]))
        scored.sort()
        return [row[2] for row in scored[:limit]]


class SQLiteBackend:
    name = 'sqlite-fts5'

    @staticmethod
    def _quote(text):
        return '"%s"' % text.replace('"', '""')

    def match_expression(self, terms, phrases, excludes):
        positive = []
        if terms:
            positive.append('(%s)' % ' OR '.join(self._quote(t) for t in terms))
        positive += [self._quote(p) for p in phrases]
        expression = ' AND '.join(positive)
        for exclude in excludes:
            expression += ' NOT ' + self._quote(exclude)
        return expression

    def rank(self, terms, phrases, excludes, limit=MAX_RESULTS):
        if not (terms or phrases):
            return []
        sql = ("SELECT rowid FROM forum_searchdocument_fts WHERE forum_searchdocument_fts MATCH %%s "
               "ORDER BY bm25(forum_searchdocument_fts, %s, %s, %s, %s), rowid DESC LIMIT %%s" % WEIGHTS)
        with connection.cursor() as cursor:
            cursor.execute(sql, [self.match_expression(terms, phrases, excludes), limit])
            return [row[0] for row in cursor.fetchall()]


class MySQLBackend:
    """MATCH ... AGAINST on the FULLTEXT indexes forum_search_title_ft (title)
    and forum_search_all_ft (title, tagnames, body, answers), migration 0013."""
    name = 'mysql-fulltext'
    BOOLEAN_OPERATORS = re.compile(r'[+\-<>()~*"@]')

    def _clean(self, text):
        return ' '.join(self.BOOLEAN_OPERATORS.sub(' ', text).split())

    def boolean_expression(self, terms, phrases, excludes):
        parts = [self._clean(t) for t in terms]
        parts += ['+"%s"' % self._clean(p) for p in phrases]
        parts += [('-"%s"' if ' ' in e else '-%s') % self._clean(e) for e in excludes]
        return ' '.join(p for p in parts if p.strip('+-"'))

    def rank(self, terms, phrases, excludes, limit=MAX_RESULTS):
        if not (terms or phrases):
            return []
        natural = ' '.join(self._clean(w) for w in list(terms) + list(phrases))
        if phrases or excludes:
            where = "MATCH(title, tagnames, body, answers) AGAINST (%s IN BOOLEAN MODE)"
            expression = self.boolean_expression(terms, phrases, excludes)
        else:
            where = "MATCH(title, tagnames, body, answers) AGAINST (%s)"
            expression = natural
        sql = ("SELECT question_id FROM forum_searchdocument WHERE " + where +
               " ORDER BY MATCH(title) AGAINST (%s) * 3 + MATCH(title, tagnames, body, answers) AGAINST (%s) DESC,"
               " question_id DESC LIMIT %s")
        with connection.cursor() as cursor:
            cursor.execute(sql, [expression, natural, natural, limit])
            return [row[0] for row in cursor.fetchall()]


class PostgresBackend:
    name = 'postgres-fts'
    CONFIG = 'english'

    def rank(self, terms, phrases, excludes, limit=MAX_RESULTS):
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
        from forum.models import SearchDocument
        if not (terms or phrases):
            return []
        vector = (SearchVector('title', weight='A', config=self.CONFIG) +
                  SearchVector('tagnames', weight='B', config=self.CONFIG) +
                  SearchVector('body', weight='C', config=self.CONFIG) +
                  SearchVector('answers', weight='D', config=self.CONFIG))
        query = None
        for term in terms:
            q = SearchQuery(term, config=self.CONFIG)
            query = q if query is None else query | q
        for phrase in phrases:
            q = SearchQuery(phrase, search_type='phrase', config=self.CONFIG)
            query = q if query is None else query & q
        for exclude in excludes:
            query = query & ~SearchQuery(exclude, search_type='phrase', config=self.CONFIG)
        documents = SearchDocument.objects.annotate(document=vector).filter(document=query).annotate(
            rank=SearchRank(vector, query)).order_by('-rank', '-question_id')
        return list(documents.values_list('question_id', flat=True)[:limit])


BACKENDS = {'sqlite': SQLiteBackend, 'mysql': MySQLBackend, 'postgresql': PostgresBackend}


def get_backend():
    """The full-text backend for this database, or keyword matching."""
    vendor = connection.vendor
    if vendor == 'sqlite' and not sqlite_fts_available():
        return KeywordBackend()
    return BACKENDS.get(vendor, KeywordBackend)()


def rank(terms, phrases, excludes, limit=MAX_RESULTS):
    """Question ids matching, best first; keyword matching if the database's
    full-text search fails (e.g. its index is missing)."""
    backend = get_backend()
    try:
        with transaction.atomic():
            return backend.rank(terms, phrases, excludes, limit)
    except Exception:
        if isinstance(backend, KeywordBackend):
            raise
        logger.exception('%s search failed, using keyword matching', backend.name)
        return KeywordBackend().rank(terms, phrases, excludes, limit)
