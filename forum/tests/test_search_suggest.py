"""The header search box's suggestions: /search/suggest/?q= (forum.views.search.suggest)."""
import json
from unittest import mock

from forum.actions import DeleteAction
from forum.models import Answer, Question, Tag
from forum.search import backends

from .base import OSQATestCase


class SearchSuggestTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        backends.reset()
        self.addCleanup(backends.reset)
        self.alice = self.create_user('alice', reputation=120)
        self.rasterfan = self.create_user('rasterfan', reputation=7)
        self.create_user('gone_raster', is_active=False)
        self.title_match = self.ask(self.alice, 'Raster layers in Earth Map', 'earthmap')
        self.body_match = self.ask(self.alice, 'Exporting survey results', 'collect',
                                   'The raster layer is missing after I export the survey results.')
        self.answer = Answer(author=self.rasterfan, parent=self.title_match, body='Upload it as an asset.')
        self.answer.save()
        Tag.objects.create(name='raster', created_by=self.alice, used_count=3)
        Tag.objects.create(name='georaster', created_by=self.alice, used_count=9)
        Tag.objects.create(name='rasterunused', created_by=self.alice, used_count=0)

    def ask(self, author, title, tags, body='How can I display my own layers on the map?'):
        question = Question(author=author, title=title, tagnames=tags, body=body)
        question.save()
        return question

    def suggest(self, q, status=200):
        response = self.client.get('/search/suggest/', {'q': q})
        self.assertEqual(response.status_code, status)
        self.assertEqual(response['Content-Type'], 'application/json')
        return response, json.loads(response.content)

    def test_shape(self):
        response, data = self.suggest('raster')
        self.assertEqual(set(data), {'q', 'search_url', 'highlight', 'questions', 'tags', 'users'})
        self.assertEqual(data['q'], 'raster')
        self.assertEqual(data['search_url'], '/search/?q=raster')
        self.assertEqual(data['highlight'], ['raster'])
        # Ranked: the title match first.
        self.assertEqual([q['title'] for q in data['questions']],
                         ['Raster layers in Earth Map', 'Exporting survey results'])
        first = data['questions'][0]
        self.assertEqual(set(first), {'title', 'url', 'meta', 'accepted'})
        self.assertEqual(first['url'], self.title_match.get_absolute_url())
        self.assertEqual(first['meta'], '1 answer')
        self.assertFalse(first['accepted'])
        self.assertEqual(data['questions'][1]['meta'], '0 answers')
        # Tags starting with the word first, unused ones left out.
        self.assertEqual([t['name'] for t in data['tags']], ['raster', 'georaster'])
        self.assertEqual(data['tags'][0], {'name': 'raster', 'url': '/tags/raster/', 'count': 3,
                                           'meta': '3 questions'})
        # Active people only, with what their profile shows publicly.
        self.assertEqual([u['name'] for u in data['users']], ['rasterfan'])
        user = data['users'][0]
        self.assertEqual(set(user), {'name', 'url', 'avatar', 'reputation', 'meta'})
        self.assertEqual(user['url'], self.rasterfan.get_profile_url())
        self.assertTrue(user['avatar'].startswith('https://secure.gravatar.com/avatar/%s?' % self.rasterfan.gravatar))
        self.assertEqual(user['reputation'], 7)
        self.assertNotIn('rasterfan@example.com', response.content.decode())
        self.assertNotIn('email', response.content.decode())

    def test_word_being_typed_and_operators(self):
        _, data = self.suggest('rast')
        self.assertEqual([q['title'] for q in data['questions']], ['Raster layers in Earth Map'])
        self.assertEqual([t['name'] for t in data['tags']], ['raster', 'georaster'])
        # A tag operator narrows the questions; the word being typed is a tag.
        _, data = self.suggest('raster [collect]')
        self.assertEqual([q['title'] for q in data['questions']], ['Exporting survey results'])
        _, data = self.suggest('tag:geo')
        self.assertEqual([t['name'] for t in data['tags']], ['georaster'])
        self.assertEqual(data['users'], [])
        _, data = self.suggest('user:ali')
        self.assertEqual([u['name'] for u in data['users']], ['alice'])
        self.assertEqual(data['tags'], [])
        # A finished word (followed by a space) isn't completed.
        _, data = self.suggest('map ')
        self.assertEqual(data['tags'], [])
        self.assertEqual(data['users'], [])

    def test_escaping(self):
        self.ask(self.alice, '<script>alert("x")</script> & <b>raster</b>', 'collect')
        response, data = self.suggest('<b>raster')
        body = response.content.decode()
        self.assertNotIn('<', body)
        self.assertNotIn('>', body)
        self.assertIn('\\u003cscript\\u003e', body)
        # The JSON still decodes to the exact text, which the page inserts as text.
        self.assertIn('<script>alert("x")</script> & <b>raster</b>', [q['title'] for q in data['questions']])
        self.assertEqual(data['q'], '<b>raster')

    def test_deleted_questions_left_out(self):
        DeleteAction(user=self.alice, node=self.title_match, ip='127.0.0.1').save()
        _, data = self.suggest('raster')
        self.assertEqual([q['title'] for q in data['questions']], ['Exporting survey results'])
        _, data = self.suggest('earth')
        self.assertEqual(data['questions'], [])

    def test_empty_and_short_queries(self):
        for q in ('', ' ', 'r', ' r '):
            with self.subTest(q=q):
                _, data = self.suggest(q)
                self.assertEqual((data['questions'], data['tags'], data['users']), ([], [], []))
        response = self.client.get('/search/suggest/')
        self.assertEqual(json.loads(response.content)['questions'], [])
        self.assertEqual(self.client.post('/search/suggest/', {'q': 'raster'}).status_code, 405)

    def test_long_query_is_cut(self):
        _, data = self.suggest('raster ' * 100)
        self.assertLessEqual(len(data['q']), 100)

    def test_answers_are_cached_briefly(self):
        response, first = self.suggest('raster')
        self.assertEqual(response['Cache-Control'], 'max-age=60')
        with mock.patch('forum.views.search.suggestions') as suggestions:
            _, again = self.suggest('Raster')  # same query, other case
            self.assertEqual(again, first)
            suggestions.assert_not_called()
            suggestions.return_value = {'q': 'other', 'search_url': '', 'highlight': [], 'questions': [],
                                        'tags': [], 'users': []}
            self.suggest('other query')
            suggestions.assert_called_once_with('other query')

    def test_anonymous_and_signed_in_get_the_same(self):
        _, anonymous = self.suggest('raster')
        self.login(self.alice)
        _, member = self.suggest('raster')
        self.assertEqual(anonymous, member)
