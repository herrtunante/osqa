from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from forum.actions import VoteUpAction
from forum.models import Answer, Comment, Node, Question

from .base import OSQATestCase


class QueryCountTest(OSQATestCase):
    """Warm-cache query budgets for the busiest pages.

    Foreign keys to users and posts must be served from the object cache, and
    per-post data (votes) loaded once per page, so these stay small and do not
    grow with the number of rows shown.
    """

    def setUp(self):
        super().setUp()
        # Settings keep their values in memory for a few seconds of wall-clock
        # time; if that ran out between the warm-up request and the measured
        # one, the measured page would re-read them and the count would vary
        # with timing. Keep them for the whole test.
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.users = [self.create_user('user%d' % i, reputation=1000) for i in range(6)]
        self.questions = []
        for i in range(20):
            question = Question(author=self.users[i % 6], title='Question number %d title' % i,
                                body='Body text', tagnames='tag%d common' % (i % 4))
            question.save()
            self.questions.append(question)

    def add_answers(self, question, count, comments=5):
        for i in range(count):
            answer = Answer(author=self.users[(i + 1) % 6], parent=question, body='Answer body %d long enough' % i)
            answer.save()
            for j in range(comments):
                Comment(author=self.users[j % 6], parent=answer, body='Comment %d %d long enough' % (i, j)).save()
            VoteUpAction(user=self.users[(i + 2) % 6], node=answer, ip='127.0.0.1').save()

    def warm_queries(self, url, user=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 200)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx.captured_queries)

    def test_list_pages(self):
        for url, user, budget in [('/', None, 8), ('/questions/', None, 6), ('/questions/', self.users[0], 12),
                                  ('/feeds/rss/', None, 5)]:
            with self.subTest(url=url, user=user):
                self.assertLessEqual(self.warm_queries(url, user), budget)

    def test_question_page(self):
        question = self.questions[0]
        self.add_answers(question, 10)
        url = question.get_absolute_url()
        self.assertLessEqual(self.warm_queries(url), 20)
        self.assertLessEqual(self.warm_queries(url, self.users[3]), 28)

    def test_question_page_does_not_scale_with_comments_or_votes(self):
        small, big = self.questions[1], self.questions[2]
        self.add_answers(small, 3)
        self.add_answers(big, 6)
        viewer = self.users[4]
        extra = self.warm_queries(big.get_absolute_url(), viewer) - self.warm_queries(small.get_absolute_url(), viewer)
        # At most one query per extra answer (its comment list); none per comment or vote.
        self.assertLessEqual(extra, 3)

    def test_question_view_keeps_count_cache(self):
        key = Node._get_cache_query_invalidation_key()
        Question.objects.count()
        before = cache.get(key)

        question = self.questions[5]
        self.client.get(question.get_absolute_url())

        self.assertEqual(cache.get(key), before)
        self.assertEqual(Question.objects.get(id=question.id).extra_count, 1)

    def test_list_view_does_not_write_preferences_every_time(self):
        self.client.force_login(self.users[0])
        self.client.get('/questions/')
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/questions/')
        writes = [q['sql'] for q in ctx.captured_queries if 'forum_userproperty' in q['sql'] and
                  q['sql'].startswith(('UPDATE', 'INSERT'))]
        self.assertEqual(writes, [])
