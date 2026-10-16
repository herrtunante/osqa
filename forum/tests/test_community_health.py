"""The community health dashboard (/admin/, forum/utils/community_health.py):
figures on a constructed forum (deleted, held and suspended authors' posts
left out), empty forums, periods, the cache and refresh, access, CSV, and
the number of queries."""
import csv
import datetime
import io
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from forum.actions import (AcceptAnswerAction, ApprovePostAction, CloseAction, DeleteAction, FlagAction,
                           QueueForReviewAction, VoteUpAction)
from forum.models import Answer, Comment, ModerationReport, Question, SuggestedEdit, User
from forum.utils import community_health as health

from .base import OSQATestCase
from .test_query_budgets import QueryBudgetMixin

NOW = datetime.datetime.now().replace(microsecond=0)


def ago(**kwargs):
    return NOW - datetime.timedelta(**kwargs)


class HealthFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', is_staff=True, is_superuser=True, email_isvalid=True)
        self.staff = self.create_user('moderator', is_staff=True, email_isvalid=True)
        self.asker = self.user('asker', ago(days=200))
        self.helper = self.user('helper', ago(days=200))
        # The settings keep their values for a few seconds: keep them for the test.
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)

    def user(self, name, joined, **fields):
        fields.setdefault('email_isvalid', True)
        user = self.create_user(name, **fields)
        User.objects.filter(id=user.id).update(date_joined=joined)
        return User.objects.get(id=user.id)

    def question(self, title, asked, tags='collect', author=None, state=''):
        question = Question(author=author or self.asker, title=title, tagnames=tags,
                            body='Body of %s, long enough.' % title)
        question.save()
        Question.objects.filter(id=question.id).update(added_at=asked, state_string=state)
        return Question.objects.get(id=question.id)

    def answer(self, question, at, author=None, state=''):
        answer = Answer(author=author or self.helper, parent=question, body='An answer that is long enough.')
        answer.save()
        Answer.objects.filter(id=answer.id).update(added_at=at, state_string=state)
        return Answer.objects.get(id=answer.id)

    def comment(self, parent, at, author=None):
        comment = Comment(author=author or self.helper, parent=parent, body='A comment long enough.')
        comment.save()
        Comment.objects.filter(id=comment.id).update(added_at=at)
        return Comment.objects.get(id=comment.id)


class EmptyForumTest(HealthFixture):
    def test_no_data(self):
        data = health.compute(30, now=NOW)
        cur, prev = data['cur'], data['prev']
        for name in ('questions', 'answers', 'comments', 'backlog', 'members', 'active_members',
                     'returning_members', 'suspensions', 'ai_reports'):
            self.assertEqual((cur[name], prev[name]), (0, 0), name)
        for name in ('median_first_answer', 'flag_resolution', 'review_resolution', 'edit_resolution'):
            self.assertIsNone(cur[name], name)
        self.assertIsNone(health.percent(cur['answered_24h']))
        self.assertEqual(len(data['daily']), 31)
        self.assertEqual(data['tags'], [])
        self.assertEqual(data['answerers'], [])
        self.assertEqual(sum(map(sum, data['heatmap'])), 0)

        groups = health.tiles(data, {'total': 0})
        values = {t['key']: t for g in groups for t in g['tiles']}
        self.assertEqual(values['questions']['value'], '0')
        self.assertEqual(values['answered_24h']['value'], '–')
        self.assertEqual(values['questions']['trend']['words'], 'no change')
        self.assertNotIn('ai_reports', values)  # no automated reports: no tile

        self.login(self.admin)
        page = self.client.get('/admin/')
        self.assertContains(page, 'Community health')
        self.assertContains(page, 'No questions were asked in this period.')
        self.assertContains(page, 'Nobody answered a question in this period.')


class FiguresTest(HealthFixture):
    def setUp(self):
        super().setUp()
        self.newbie = self.user('newbie', ago(days=10), email_isvalid=False)
        self.joiner = self.user('joiner', ago(days=12))
        self.fresh = self.user('fresh', ago(days=2))
        self.spammer = self.user('spammer', ago(days=5), is_active=False)

        # This period (the last 30 days).
        self.q1 = self.question('Answered after 2 hours', ago(days=10), tags='collect export')
        a1 = self.answer(self.q1, ago(days=10) + datetime.timedelta(hours=2))
        AcceptAnswerAction(user=self.asker, node=a1, ip='127.0.0.1').save()
        self.answer(self.q1, ago(days=9), author=self.joiner)
        self.q2 = self.question('Answered after 30 hours', ago(days=9), tags='export')
        self.answer(self.q2, ago(days=9) + datetime.timedelta(hours=30))
        self.q3 = self.question('Only the asker answered', ago(days=8), tags='export')
        self.answer(self.q3, ago(days=8) + datetime.timedelta(hours=1), author=self.asker)
        self.q4 = self.question('Asked two hours ago', ago(hours=2), author=self.joiner)
        self.comment(self.q4, ago(hours=1))
        # Left out: deleted, held for review, by a suspended account, answers under them.
        deleted = self.question('Deleted question', ago(days=3), tags='spam')
        DeleteAction(user=self.staff, node=deleted, ip='127.0.0.1').save()
        self.answer(deleted, ago(days=3))
        held = self.question('Held question', ago(days=3), tags='spam', state='(pending_review)')
        self.answer(held, ago(days=2))
        self.question('Spam question', ago(days=3), author=self.spammer, tags='spam')
        self.answer(self.q2, ago(days=1), state='(pending_review)')
        self.answer(self.q3, ago(days=1), author=self.spammer)
        # Published while waiting for review ("after" mode): counts.
        self.comment(self.q2, ago(days=1), author=self.newbie)

        # The previous period (30 to 60 days ago).
        self.q5 = self.question('Old, answered after an hour', ago(days=40), tags='collect')
        self.answer(self.q5, ago(days=40) + datetime.timedelta(hours=1))
        self.q6 = self.question('Old, answered 20 days ago', ago(days=50), tags='collect')
        self.answer(self.q6, ago(days=20))

        self._as_admin()
        VoteUpAction(user=self.newbie, node=self.q1, ip='127.0.0.1').save()

    def _as_admin(self):
        import forum
        from django.test import RequestFactory
        request = RequestFactory().post('/')
        request.user = self.admin
        forum.REQUEST_HOLDER.request = request
        self.addCleanup(setattr, forum.REQUEST_HOLDER, 'request', None)

    def test_posts_and_answering(self):
        data = health.compute(30, now=NOW)
        cur, prev = data['cur'], data['prev']
        self.assertEqual((cur['questions'], prev['questions']), (4, 2))
        # q1 x2, q2, q3 (by the asker) and q6's late answer; not the held, deleted or spam ones.
        self.assertEqual((cur['answers'], prev['answers']), (5, 1))
        self.assertEqual((cur['comments'], prev['comments']), (2, 0))
        # Questions old enough: q1, q2, q3 for 24 hours (q4 is two hours old) and for 7 days.
        self.assertEqual(cur['answered_24h'], {'num': 1, 'den': 3})
        self.assertEqual(cur['answered_7d'], {'num': 2, 'den': 3})
        self.assertAlmostEqual(cur['median_first_answer'], 16.0, places=3)
        self.assertEqual(cur['accepted'], {'num': 1, 'den': 4})
        self.assertEqual(prev['answered_24h'], {'num': 1, 'den': 2})
        self.assertAlmostEqual(prev['median_first_answer'], (1 + 30 * 24) / 2.0, places=3)
        # Backlog: q3 and q4 now; q6 (answered 20 days ago) when the period began.
        self.assertEqual((cur['backlog'], prev['backlog']), (2, 1))
        CloseAction(user=self.staff, node=self.q4, extra='off topic', ip='127.0.0.1').save()
        self.assertEqual(health.compute(30, now=NOW)['cur']['backlog'], 1)

    def test_distribution_heatmap_and_days(self):
        data = health.compute(30, now=NOW)
        buckets = dict(data['buckets'])
        self.assertEqual(buckets['3h'], 1)    # 2 hours
        self.assertEqual(buckets['3d'], 1)    # 30 hours
        self.assertEqual(buckets['none'], 2)  # q3 and q4
        self.assertEqual(sum(buckets.values()), 4)
        self.assertEqual(sum(map(sum, data['heatmap'])), 4)
        asked = self.q1.added_at
        self.assertGreaterEqual(data['heatmap'][asked.weekday()][asked.hour], 1)
        self.assertEqual(sum(row['questions'] for row in data['daily']), 4)
        self.assertEqual(sum(row['answers'] for row in data['daily']), 5)
        self.assertEqual(sum(row['members'] for row in data['daily']), 3)  # newbie, joiner, fresh

    def test_members(self):
        cur = health.compute(30, now=NOW)['cur']
        self.assertEqual(cur['members'], 3)  # not the suspended one
        self.assertEqual(cur['confirmed_email'], {'num': 2, 'den': 3})
        # Old enough: joiner (an answer 3 days after joining) and newbie (a comment 9 days
        # after); "fresh" joined two days ago.
        self.assertEqual(cur['posted_7d'], {'num': 1, 'den': 2})
        self.comment(self.q1, ago(days=8), author=self.newbie)
        self.assertEqual(health.compute(30, now=NOW)['cur']['posted_7d'], {'num': 2, 'den': 2})
        # Posted or voted: asker, helper, joiner, newbie (the vote and the comment).
        self.assertEqual(cur['active_members'], 4)
        self.assertEqual(cur['returning_members'], 2)  # asker and helper posted in both periods

    def test_tags_and_answerers(self):
        data = health.compute(30, now=NOW)
        self.assertEqual([(t['name'], t['cur'], t['prev']) for t in data['tags']], [('export', 3, 0)])
        self.assertEqual([(r['username'], r['answers'], r['accepted']) for r in data['answerers']],
                         [('helper', 3, 1), ('asker', 1, 0), ('joiner', 1, 0)])
        charts = health.chart_data(data)
        self.assertEqual(charts['answerers'][0]['url'], self.helper.get_profile_url())

    def test_periods(self):
        self.assertEqual(health.compute(7, now=NOW)['cur']['questions'], 1)
        seven = health.compute(7, now=NOW)
        self.assertEqual(seven['cur']['answered_7d']['den'], 0)  # too young to tell
        self.assertEqual(health.compute(90, now=NOW)['cur']['questions'], 6)
        for value, days in (('7', 7), ('90', 90), ('45', 30), ('x', 30), (None, 30)):
            self.assertEqual(health.parse_days(value), days)

    def test_moderation(self):
        flagger = self.user('flagger', ago(days=300), reputation=500)
        flag = FlagAction(user=flagger, node=self.q2, extra='spam', ip='127.0.0.1').save()
        from forum.models import Action
        Action.objects.filter(id=flag.id).update(action_date=ago(days=5))
        Action.objects.get(id=flag.id).cancel(self.staff)
        flag2 = FlagAction(user=flagger, node=self.q3, extra='spam', ip='127.0.0.1').save()
        Action.objects.filter(id=flag2.id).update(action_date=ago(hours=3))
        DeleteAction(user=self.staff, node=self.q3, ip='127.0.0.1').save()

        queued = QueueForReviewAction(user=self.joiner, node=self.q4, ip='127.0.0.1', extra='unreviewed').save()
        Action.objects.filter(id=queued.id).update(action_date=ago(hours=2))
        Question.objects.filter(id=self.q4.id).update(state_string='(unreviewed)')
        ApprovePostAction(user=self.staff, node=Question.objects.get(id=self.q4.id), ip='127.0.0.1').save()

        edit = SuggestedEdit.objects.create(node=self.q1, author=self.newbie, body='Better body', summary='typo',
                                            status=SuggestedEdit.APPROVED, created_at=ago(hours=10),
                                            reviewed_at=ago(hours=4))
        self.assertTrue(edit.id)
        ModerationReport.objects.create(node=self.q1, source='ai', verdict='spam', created_at=ago(days=1))

        data = health.compute(30)  # resolved just now, after NOW
        cur = data['cur']
        self.assertEqual(cur['flag_resolution_count'], 2)
        self.assertEqual(cur['review_resolution_count'], 1)
        self.assertAlmostEqual(cur['review_resolution'], 2.0, delta=0.05)
        self.assertAlmostEqual(cur['edit_resolution'], 6.0, places=3)
        self.assertEqual((cur['ai_reports'], data['ai_reports_open']), (1, 1))
        values = {t['key']: t for g in health.tiles(data, {'total': 3, 'flags': 1, 'review': 2})
                  for t in g['tiles']}
        self.assertEqual(values['waiting']['value'], '3')
        self.assertIn('2 first posts', values['waiting']['note'])
        self.assertIn('ai_reports', values)

    def test_trends(self):
        self.assertEqual(health._trend('count', 12, 10, 'up')['text'], '+20%')
        self.assertEqual(health._trend('count', 12, 10, 'up')['tone'], 'good')
        self.assertEqual(health._trend('count', 12, 10, 'down')['tone'], 'bad')
        self.assertEqual(health._trend('count', 5, 0, 'up')['text'], 'new')
        self.assertEqual(health._trend('rate', 40, 50, 'up')['text'], '−10 points')
        self.assertEqual(health._trend('hours', 3.0, 3.0, 'down')['words'], 'no change')
        self.assertIsNone(health._trend('hours', None, 3.0, 'down'))

    def test_dashboard_page(self):
        self.login(self.staff)
        page = self.client.get('/admin/?days=7')
        self.assertContains(page, 'aria-current="page">7 days</a>', html=False)
        self.assertContains(page, 'How these are counted')
        self.assertContains(page, 'id="health-daily-data"')
        self.assertContains(page, 'href="%s"' % self.helper.get_profile_url())
        self.assertNotContains(page, 'Site status')  # administrators only
        self.assertNotContains(page, 'Spam question')
        self.login(self.admin)
        page = self.client.get('/admin/?days=90')
        self.assertContains(page, 'Site status')
        self.assertContains(page, 'aria-current="page">90 days</a>', html=False)

    def test_access(self):
        self.assertIn(self.client.get('/admin/').status_code, (302, 401, 403))
        self.login(self.joiner)
        self.assertIn(self.client.get('/admin/').status_code, (401, 403))
        self.assertIn(self.client.get('/admin/?format=csv').status_code, (401, 403))

    def test_cache_and_refresh(self):
        self.login(self.staff)
        first = health.figures(30)
        with CaptureQueriesContext(connection) as ctx:
            again = health.figures(30)
        self.assertEqual(len(ctx.captured_queries), 0)
        self.assertEqual(first['computed_at'], again['computed_at'])
        self.question('Asked just now', ago(minutes=1))
        self.assertEqual(health.figures(30)['cur']['questions'], 4)  # cached
        response = self.client.post('/admin/?days=30', {'refresh': '1'})
        self.assertRedirects(response, '/admin/?days=30', fetch_redirect_response=False)
        self.assertEqual(health.figures(30)['cur']['questions'], 5)
        self.assertIsNone(cache.get(health.CACHE_KEY % 7))

    def test_csv(self):
        self.user('=cmd', ago(days=300))
        self.answer(self.q2, ago(days=2), author=User.objects.get(username='=cmd'))
        self.login(self.staff)
        response = self.client.get('/admin/?days=30&format=csv')
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="community-health-30d-', response['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], ['Section', 'Figure', 'Last 30 days', 'Previous 30 days'])
        self.assertIn(['Activity', 'New questions', '4', '2'], rows)
        self.assertIn("'=cmd", [row[0] for row in rows if row])
        self.assertNotIn('=cmd', [row[0] for row in rows if row])


class HealthQueryBudgetTest(QueryBudgetMixin, FiguresTest):
    """The figures are a fixed number of aggregate queries, whatever the number of posts."""

    def test_compute_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            health.compute(30, now=NOW)
        small = len(ctx.captured_queries)
        for n in range(10):
            q = self.question('More %d' % n, ago(days=n + 1), tags='more%d' % n)
            self.answer(q, ago(days=n + 1) + datetime.timedelta(hours=n))
        with CaptureQueriesContext(connection) as ctx:
            health.compute(30, now=NOW)
        self.assertEqual(len(ctx.captured_queries), small)
        self.assertLessEqual(small, 13)

    def test_dashboard_budget(self):
        # Cold: the 13 figure queries, the inbox counts, the layout and recent
        # activity (about 20 of them, measured before this dashboard: 41 and 48
        # cold); warm: the figures come from the cache (12 and 16).
        self.assertQueryBudget('/admin/', cold=43, warm=14, user=self.staff)
        self.assertQueryBudget('/admin/?days=7', cold=50, warm=18, user=self.admin)

    # The fixture's own tests run in FiguresTest.
    test_posts_and_answering = test_distribution_heatmap_and_days = test_members = None
    test_tags_and_answerers = test_periods = test_moderation = test_trends = None
    test_dashboard_page = test_access = test_cache_and_refresh = test_csv = None
