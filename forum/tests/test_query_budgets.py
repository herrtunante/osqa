"""Query budgets for the busiest pages, measured with a cold and a warm cache.

Each budget is an upper bound set just above the count measured when it was
last tuned; a page that starts running a query per row (an N+1) goes over it.
`QUERY_BUDGET_REPORT=1` prints the measured counts instead of failing
(`QUERY_BUDGET_REPORT=sql` also lists the queries, grouped by shape).

"Cold" is the first request after cache.clear() in a process that has already
served the page once (settings and templates loaded): what a page costs after
the shared cache was emptied or its entries expired. "Warm" is the next one.
"""
import collections
import json
import os
import re
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from forum.actions import AwardAction, VoteUpAction
from forum.models import Answer, Badge, Comment, Question
from forum.models.notification import Notification

from .base import OSQATestCase

REPORT = os.environ.get('QUERY_BUDGET_REPORT')


class QueryBudgetMixin:
    """`assertQueryBudget(url, cold=n, warm=m)`: at most n queries with an empty
    cache and at most m once the cache holds what the first request stored."""

    def count_queries(self, method, url, data=None, user=None, **extra):
        self.client.logout()
        if user:
            self.client.force_login(user)
        with CaptureQueriesContext(connection) as ctx:
            if method == 'post':
                response = self.client.post(url, data, **extra)
            else:
                response = self.client.get(url, **extra)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx.captured_queries), ctx

    def assertQueryBudget(self, url, cold, warm, user=None, method='get', data=None, label=None, **extra):
        # One request first so the process-level state (settings kept in
        # memory, templates, URL resolvers) is loaded, then an empty cache.
        self.count_queries(method, url, data, user, **extra)
        cache.clear()
        cold_count, cold_ctx = self.count_queries(method, url, data, user, **extra)
        warm_count, warm_ctx = self.count_queries(method, url, data, user, **extra)
        name = label or '%s %s%s' % (method.upper(), url, ' as %s' % user.username if user else '')
        if REPORT:
            print('\nQUERIES %-50s cold=%3d warm=%3d' % (name, cold_count, warm_count))
            if REPORT == 'sql':
                for title, ctx in (('cold', cold_ctx), ('warm', warm_ctx)):
                    shapes = collections.Counter(re.sub(r"\d+|'[^']*'", '?', q['sql'])[:300]
                                                 for q in ctx.captured_queries)
                    for shape, n in shapes.most_common():
                        print('  %s %3d  %s' % (title, n, shape))
            return
        self.assertLessEqual(cold_count, cold, '%s, cold cache:\n%s' % (name, _sql(cold_ctx)))
        self.assertLessEqual(warm_count, warm, '%s, warm cache:\n%s' % (name, _sql(warm_ctx)))


def _sql(ctx):
    return '\n'.join(q['sql'][:200] for q in ctx.captured_queries)


class PageQueryBudgetTest(QueryBudgetMixin, OSQATestCase):

    def setUp(self):
        super().setUp()
        # Settings keep their values in memory for a few seconds; keep them for
        # the whole test so the count doesn't depend on timing.
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.users = [self.create_user('user%d' % i, reputation=1000 + i) for i in range(8)]
        self.questions = []
        for i in range(20):
            question = Question(author=self.users[i % 8], title='Question number %d title' % i,
                                body='Body text of question %d' % i, tagnames='tag%d common' % (i % 5))
            question.save()
            self.questions.append(question)
        # A question with 10 answers and 20 comments, votes on the answers.
        self.big = self.questions[0]
        for i in range(10):
            answer = Answer(author=self.users[(i + 1) % 8], parent=self.big, body='Answer body %d long enough' % i)
            answer.save()
            for j in range(2):
                Comment(author=self.users[(i + j) % 8], parent=answer,
                        body='Comment %d %d long enough to keep' % (i, j)).save()
            VoteUpAction(user=self.users[(i + 2) % 8], node=answer, ip='127.0.0.1').save()
        # Answers on a few list questions, so "last activity by" differs.
        for question in self.questions[1:6]:
            Answer(author=self.users[7], parent=question, body='Another answer long enough').save()
        badge = Badge.objects.all().first()
        if badge:
            for user in self.users[:4]:
                AwardAction(user=user, node=self.big).save(data=dict(badge=badge, trigger=None))
        for i in range(3):
            Notification.objects.create(user=self.users[1], kind='comment', node=self.big, actor=self.users[2])
        self.viewer = self.users[3]

    def test_home_and_question_lists(self):
        self.assertQueryBudget('/', cold=17, warm=8)
        self.assertQueryBudget('/', cold=24, warm=14, user=self.viewer)
        self.assertQueryBudget('/questions/', cold=14, warm=7)
        self.assertQueryBudget('/questions/', cold=21, warm=13, user=self.viewer)
        self.assertQueryBudget('/questions/unanswered/', cold=14, warm=7)
        self.assertQueryBudget('/tags/common/', cold=17, warm=10)

    def test_question_page(self):
        url = self.big.get_absolute_url()
        self.assertQueryBudget(url, cold=40, warm=14)
        self.assertQueryBudget(url, cold=51, warm=25, user=self.viewer)

    def test_question_page_does_not_grow_with_answers_and_comments(self):
        small = self.questions[1]  # one answer, no comments
        counts = []
        for question in (small, self.big):
            self.client.get(question.get_absolute_url())
            cache.clear()
            counts.append(self.count_queries('get', question.get_absolute_url())[0])
        # The big page has 9 more answers and 20 comments by 7 more authors:
        # at most one query per extra author (accept rate, user row), none per post.
        self.assertLessEqual(counts[1] - counts[0], 16, counts)

    def test_live_question_updates(self):
        # The live question page's poll (forum/views/live.py): the snapshot, the
        # posts rendered for "Show", and the 304 of an unchanged question.
        from forum.utils import live
        url = '/questions/%d/updates/?since=%s' % (self.big.id, live.now_token())
        self.assertQueryBudget(url, cold=7, warm=6)  # anonymous: includes creating the session
        self.assertQueryBudget(url, cold=7, warm=5, user=self.viewer)
        posts = ','.join(str(answer.id) for answer in Answer.objects.filter(parent=self.big)[:3])
        self.assertQueryBudget('%s&posts=%s,%d' % (url, posts, self.big.id), cold=24, warm=12, user=self.viewer,
                               label='GET live posts as viewer')
        for user, budget in ((None, 1), (self.viewer, 2)):
            self.count_queries('get', url, user=user)  # signs in, if at all
            etag = self.client.get(url)['ETag']
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(url, HTTP_IF_NONE_MATCH=etag)
            self.assertEqual(response.status_code, 304)
            # The session (and the signed-in user): the middleware's, none of the view's.
            if REPORT:
                print('\nQUERIES %-50s 304=%3d' % ('live updates as %s' % user, len(ctx.captured_queries)))
            else:
                self.assertLessEqual(len(ctx.captured_queries), budget, _sql(ctx))

    def test_tags_users_profile(self):
        self.assertQueryBudget('/tags/', cold=7, warm=6)
        self.assertQueryBudget('/users/', cold=7, warm=6)
        self.assertQueryBudget(self.users[1].get_profile_url(), cold=23, warm=12)
        self.assertQueryBudget(self.users[1].get_profile_url(), cold=28, warm=17, user=self.users[1])

    def test_notification_count(self):
        self.assertQueryBudget('/notifications/count/', cold=5, warm=4, user=self.users[1])

    def test_agent_endpoints(self):
        self.assertQueryBudget('/llms.txt', cold=6, warm=6)
        self.assertQueryBudget('/questions/%d.md' % self.big.id, cold=10, warm=8)
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'get_question', 'arguments': {'id': self.big.id}}})
        self.assertQueryBudget('/mcp', cold=10, warm=8, method='post', data=body, content_type='application/json',
                               HTTP_ACCEPT='application/json, text/event-stream', label='MCP get_question')
