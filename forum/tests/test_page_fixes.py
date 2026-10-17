import datetime
import json
import re

from forum.actions import VoteUpAction
from forum.models import Question, User
from forum.views.users import reputation_graph_points

from .base import OSQATestCase


class FocusedAnswerTest(OSQATestCase):
    def test_bad_focused_answer_id_is_ignored(self):
        alice = self.create_user('alice')
        question = Question(author=alice, title='A question title', body='Body text', tagnames='one')
        question.save()
        for value in ('abc', '', '12x', '7'):
            with self.subTest(value=value):
                self.assertCleanPage(self.client.get(question.get_absolute_url(), {'focusedAnswerId': value}))


class ReputationGraphDataTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob', reputation=1000)
        self.question = Question(author=self.alice, title='A question title', body='Body text', tagnames='one')
        self.question.save()

    def test_running_total_ends_at_current_reputation(self):
        # Reputation no change accounts for (set by an administrator or
        # imported) is the starting value.
        User.objects.filter(pk=self.alice.pk).update(reputation=20)
        VoteUpAction(user=self.bob, node=self.question, ip='127.0.0.1').save()
        alice = User.objects.get(pk=self.alice.pk)
        changes = list(alice.reputes.order_by('date').values_list('value', flat=True))
        self.assertTrue(changes)

        later = datetime.datetime.now() + datetime.timedelta(hours=1)
        points = reputation_graph_points(alice, now=later)
        values = [y for _x, y in points]
        self.assertEqual(values[-1], alice.reputation)
        self.assertEqual(values[-1] - values[-2], 0)  # the point for "now" repeats the total
        # Every step is one change (the first point is the starting value
        # when the account is older than its first change).
        steps = [b - a for a, b in zip(values, values[1:-1])]
        self.assertEqual(steps[-len(changes):], changes)
        xs = [x for x, _y in points]
        self.assertEqual(xs, sorted(xs))

    def test_page_passes_the_points(self):
        VoteUpAction(user=self.bob, node=self.question, ip='127.0.0.1').save()
        alice = User.objects.get(pk=self.alice.pk)
        response = self.client.get(alice.get_profile_url() + '/reputation/')
        self.assertCleanPage(response)
        data = re.search(r'data-points="([^"]*)"', response.content.decode()).group(1)
        points = json.loads(data.replace('&quot;', '"'))
        self.assertEqual(points[-1][1], alice.reputation)

    def test_no_points_without_changes(self):
        carol = self.create_user('carol', reputation=5)
        self.assertEqual(reputation_graph_points(carol), [])


class DefaultStaticTextTest(OSQATestCase):
    def test_faq_headings_are_rendered(self):
        response = self.client.get('/faq/')
        self.assertCleanPage(response)
        html = response.content.decode()
        self.assertIn('<h2>Why are email notifications so important?</h2>', html)
        self.assertNotIn('**', html)
        self.assertNotRegex(html, r'\|REP_[A-Z_]+\|')

    def test_about_and_privacy(self):
        about = self.client.get('/about/')
        self.assertCleanPage(about)
        self.assertNotIn('**', about.content.decode())
        self.assertContains(about, 'href="/faq/"')
        privacy = self.client.get('/privacy/')
        self.assertCleanPage(privacy)
        self.assertNotContains(privacy, 'general message about privacy')
        self.assertNotContains(privacy, 'cookie policy details')
