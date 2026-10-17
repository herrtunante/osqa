from django.db import models


class SearchDocument(models.Model):
    """The searchable text of one question: its title, tags, the plain text of
    its body and of its answers (not deleted). Kept up to date by
    forum.search.index when posts are saved or deleted, and rebuilt by
    `manage.py rebuild_search_index`. Deleted questions have no document.

    The full-text index on this table is database specific (migration 0013):
    FULLTEXT indexes on MySQL, an FTS5 table kept in step by triggers on
    SQLite, nothing on PostgreSQL (vectors are computed at query time).

    On SQLite, a migration that alters this table makes Django rebuild it,
    which drops the FTS5 triggers: run rebuild_search_index afterwards (it
    creates them again).
    """
    question = models.OneToOneField('Node', primary_key=True, related_name='search_document',
                                    on_delete=models.CASCADE)
    title = models.CharField(max_length=300)
    tagnames = models.CharField(max_length=125, blank=True)
    body = models.TextField(blank=True)
    answers = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'forum'

    def __str__(self):
        return self.title
