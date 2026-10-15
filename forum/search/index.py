"""Keeps SearchDocument rows in step with the posts.

Node.save() and Node.delete() call node_saved() / node_deleted(); a question
is indexed again when its title, body, tags or state change, or when one of
its answers is added, edited, deleted or moved. Deleted questions have no
document. rebuild() indexes everything again (manage.py rebuild_search_index).
"""
import datetime
import logging

from django.db import connection, transaction

from .text import MAX_FIELD, html_to_text

logger = logging.getLogger('forum.search')

# Node fields whose change alters what is indexed.
INDEXED_FIELDS = {'title', 'body', 'tagnames', 'state_string', 'node_type', 'parent', 'abs_parent'}


def _documents(questions):
    """SearchDocument objects for (id, title, tagnames, body) rows."""
    from forum.models import Node, SearchDocument
    ids = [q[0] for q in questions]
    answers = {}
    for parent_id, body in Node.objects.filter(parent_id__in=ids, node_type='answer').exclude(
            state_string__contains='(deleted)').order_by('id').values_list('parent_id', 'body'):
        answers.setdefault(parent_id, []).append(html_to_text(body))
    return [SearchDocument(question_id=qid, title=(title or '')[:300], tagnames=(tagnames or '')[:125],
                           body=html_to_text(body), answers=' '.join(answers.get(qid, []))[:2 * MAX_FIELD])
            for qid, title, tagnames, body in questions]


def index_question(question_id):
    """Writes (or removes) the document of one question."""
    from forum.models import Node, SearchDocument
    if not question_id:
        return
    try:
        with transaction.atomic():
            row = Node.objects.filter(id=question_id, node_type='question').exclude(
                state_string__contains='(deleted)').values_list('id', 'title', 'tagnames', 'body').first()
            if row is None:
                SearchDocument.objects.filter(question_id=question_id).delete()
                return
            document = _documents([row])[0]
            updated = SearchDocument.objects.filter(question_id=question_id).update(
                title=document.title, tagnames=document.tagnames, body=document.body, answers=document.answers,
                updated_at=datetime.datetime.now())
            if not updated:
                document.save(force_insert=True)
    except Exception:
        # Search must never stop a post from being saved; rebuild_search_index repairs.
        logger.exception('Could not update the search index for question %s', question_id)


def node_saved(node, dirty, created=False, old_parent_id=None):
    """Called by Node.save() with the names of the fields it changed."""
    if not created and not (set(dirty) & INDEXED_FIELDS):
        return
    if node.node_type == 'question':
        index_question(node.id)
    elif 'node_type' in dirty:
        index_question(node.id)  # was a question: removes its document
    if node.node_type == 'answer' or 'node_type' in dirty or 'parent' in dirty:
        index_question(node.abs_parent_id or node.parent_id)
    if 'parent' in dirty:
        index_question(old_parent_id)


def node_deleted(node_id, node_type, parent_id):
    """Called by Node.delete() after the row is gone."""
    if node_type == 'question':
        index_question(node_id)
    elif node_type == 'answer':
        index_question(parent_id)


# -- rebuilding --------------------------------------------------------------

SQLITE_FTS = 'forum_searchdocument_fts'

# Kept in step with migration 0013 (which can't import this module).
SQLITE_SCHEMA = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS forum_searchdocument_fts USING fts5("
    "title, tagnames, body, answers, content='forum_searchdocument', content_rowid='question_id', "
    "tokenize='porter unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS forum_searchdocument_ai AFTER INSERT ON forum_searchdocument BEGIN "
    "INSERT INTO forum_searchdocument_fts(rowid, title, tagnames, body, answers) "
    "VALUES (new.question_id, new.title, new.tagnames, new.body, new.answers); END",
    "CREATE TRIGGER IF NOT EXISTS forum_searchdocument_ad AFTER DELETE ON forum_searchdocument BEGIN "
    "INSERT INTO forum_searchdocument_fts(forum_searchdocument_fts, rowid, title, tagnames, body, answers) "
    "VALUES ('delete', old.question_id, old.title, old.tagnames, old.body, old.answers); END",
    "CREATE TRIGGER IF NOT EXISTS forum_searchdocument_au AFTER UPDATE ON forum_searchdocument BEGIN "
    "INSERT INTO forum_searchdocument_fts(forum_searchdocument_fts, rowid, title, tagnames, body, answers) "
    "VALUES ('delete', old.question_id, old.title, old.tagnames, old.body, old.answers); "
    "INSERT INTO forum_searchdocument_fts(rowid, title, tagnames, body, answers) "
    "VALUES (new.question_id, new.title, new.tagnames, new.body, new.answers); END",
]


def ensure_sqlite_schema():
    """Creates the FTS5 table and its triggers if missing; False without FTS5."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            for statement in SQLITE_SCHEMA:
                cursor.execute(statement)
        return True
    except Exception as e:
        logger.warning('SQLite FTS5 is not available (%s): search uses keyword matching', e)
        return False


SQLITE_DROP = [
    "DROP TRIGGER IF EXISTS forum_searchdocument_ai",
    "DROP TRIGGER IF EXISTS forum_searchdocument_ad",
    "DROP TRIGGER IF EXISTS forum_searchdocument_au",
    "DROP TABLE IF EXISTS forum_searchdocument_fts",
]


def rebuild(batch_size=500, stdout=None):
    """Indexes every question that isn't deleted; returns the number indexed.
    On SQLite the FTS5 table and its triggers are created afresh."""
    from forum.models import Node, SearchDocument
    from . import backends
    sqlite = connection.vendor == 'sqlite'
    questions = Node.objects.filter(node_type='question').exclude(state_string__contains='(deleted)').order_by('id')
    total = 0
    with transaction.atomic():
        if sqlite:
            with connection.cursor() as cursor:
                for statement in SQLITE_DROP:
                    cursor.execute(statement)
        SearchDocument.objects.all().delete()
        last_id = 0
        while True:
            rows = list(questions.filter(id__gt=last_id).values_list('id', 'title', 'tagnames', 'body')[:batch_size])
            if not rows:
                break
            SearchDocument.objects.bulk_create(_documents(rows))
            total += len(rows)
            last_id = rows[-1][0]
            if stdout:
                stdout.write('%d questions...' % total)
    if sqlite:
        backends.reset()
        if ensure_sqlite_schema():
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO %s(%s) VALUES ('rebuild')" % (SQLITE_FTS, SQLITE_FTS))
                cursor.execute("INSERT INTO %s(%s) VALUES ('optimize')" % (SQLITE_FTS, SQLITE_FTS))
        backends.reset()
    return total
