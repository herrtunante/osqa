import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection

from forum.actions import AcceptAnswerAction, DeleteAction, ReviseAction
from forum.models import Answer, Question, SearchDocument
from forum.search import backends, engine
from forum.search.query import parse
from forum.search.text import highlighter, html_to_text, snippet

from .base import OSQATestCase


class SearchFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        backends.reset()
        self.addCleanup(backends.reset)
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob')
        # "raster" only in the body of the first, in the title of the second.
        self.body_only = self.ask(self.alice, 'Exporting survey results', 'collect',
                                  'The raster layer is missing after I export the survey results.')
        self.title_match = self.ask(self.bob, 'Raster layers in Earth Map', 'earthmap',
                                    'How can I display my own layers on the map?')
        self.csv = self.ask(self.alice, 'How do I export Collect data to CSV?', 'collect export',
                            'I need a spreadsheet of my survey data.')
        self.answer = Answer(author=self.bob, parent=self.csv, body='Use the *backup* option in the designer.')
        self.answer.save()
        AcceptAnswerAction(user=self.alice, node=self.answer, ip='127.0.0.1').save()

    def ask(self, author, title, tags, body):
        question = Question(author=author, title=title, tagnames=tags, body=body)
        question.save()
        return question

    def ids(self, text):
        questions, ranked = engine.search(text)
        return list(questions.order_by('search_rank' if ranked else '-added_at').values_list('id', flat=True))


class QueryParsingTest(OSQATestCase):
    def test_operators(self):
        query = parse('Export "survey data" -excel [collect] tag:Export is:answered user:alice is:nonsense')
        self.assertEqual(query.terms, ['export', 'nonsense'])
        self.assertEqual(query.phrases, ['survey data'])
        self.assertEqual(query.excludes, ['excel'])
        self.assertEqual(query.tags, ['collect', 'export'])
        self.assertEqual(query.flags, ['answered'])
        self.assertEqual(query.users, ['alice'])
        self.assertEqual(query.title_text, 'Export survey data is:nonsense')

    def test_stopwords_and_odd_input(self):
        self.assertEqual(parse('how do I export my data').terms, ['export', 'data'])
        self.assertEqual(parse('how to').terms, ['how', 'to'])  # nothing else: keep them
        self.assertEqual(parse('"unterminated phrase here').phrases, ['unterminated phrase here'])
        self.assertEqual(parse('user:"John Smith"').users, ['John Smith'])
        self.assertTrue(parse('').is_empty)
        self.assertEqual(parse('http://example.org/x').terms, ['http', 'example', 'org', 'x'])
        self.assertTrue(parse('-excel').has_filters)
        self.assertFalse(parse('-excel').has_text)


class FullTextSearchTest(SearchFixture):
    def test_sqlite_uses_fts5(self):
        self.assertEqual(connection.vendor, 'sqlite')
        self.assertEqual(backends.get_backend().name, 'sqlite-fts5')

    def test_title_match_ranks_above_body_only_match(self):
        self.assertEqual(self.ids('raster')[:2], [self.title_match.id, self.body_only.id])

    def test_answers_and_tags_are_searched(self):
        self.assertEqual(self.ids('backup designer'), [self.csv.id])
        self.assertIn(self.title_match.id, self.ids('earthmap'))

    def test_stemming(self):
        self.assertEqual(set(self.ids('exports')), {self.csv.id, self.body_only.id})

    def test_phrase_and_exclusion(self):
        self.assertEqual(self.ids('"survey results"'), [self.body_only.id])
        self.assertEqual(self.ids('"results survey"'), [])
        self.assertEqual(self.ids('export -spreadsheet'), [self.body_only.id])
        self.assertEqual(set(self.ids('-raster')), {self.csv.id})

    def test_filters(self):
        self.assertEqual(self.ids('export [collect]'), self.ids('export tag:collect'))
        self.assertEqual(set(self.ids('[collect]')), {self.csv.id, self.body_only.id})
        self.assertEqual(self.ids('[nosuchtag]'), [])
        self.assertEqual(set(self.ids('export is:accepted')), {self.csv.id})
        self.assertEqual(set(self.ids('export is:answered')), {self.csv.id})
        self.assertEqual(set(self.ids('export is:unanswered')), {self.body_only.id})
        self.assertEqual(set(self.ids('user:bob')), {self.title_match.id})
        self.assertEqual(set(self.ids('raster user:ALICE')), {self.body_only.id})

    def test_tag_synonyms(self):
        from forum.models import Tag, TagSynonym
        TagSynonym.objects.create(source_name='odk-collect', target=Tag.objects.get(name='collect'))
        self.assertEqual(set(self.ids('[odk-collect]')), {self.csv.id, self.body_only.id})


class IndexUpdateTest(SearchFixture):
    def document(self, question):
        return SearchDocument.objects.filter(question_id=question.id).first()

    def test_new_question_and_answer_are_indexed(self):
        self.assertEqual(self.document(self.csv).title, 'How do I export Collect data to CSV?')
        self.assertIn('Use the backup option in the designer.', self.document(self.csv).answers)
        self.assertNotIn('<em>', self.document(self.csv).answers)

    def test_edit(self):
        ReviseAction(user=self.bob, node=self.title_match, ip='127.0.0.1').save(data=dict(
            title='Vector tiles in Earth Map', text='Showing vector tiles.', tags='earthmap tiles', summary=''))
        self.assertEqual(self.ids('raster'), [self.body_only.id])
        self.assertEqual(self.ids('vector tiles'), [self.title_match.id])
        self.assertEqual(self.document(self.title_match).tagnames, 'earthmap tiles')

    def test_delete_question_and_answer(self):
        DeleteAction(user=self.bob, node=self.answer, ip='127.0.0.1').save()
        self.assertEqual(self.ids('backup'), [])
        DeleteAction(user=self.alice, node=self.body_only, ip='127.0.0.1').save()
        self.assertIsNone(self.document(self.body_only))
        self.assertEqual(self.ids('raster'), [self.title_match.id])

    def test_hard_delete(self):
        self.answer.delete()
        self.assertEqual(self.ids('backup'), [])
        Question.objects.get(id=self.csv.id).delete()
        self.assertIsNone(self.document(self.csv))
        self.assertEqual(self.ids('spreadsheet'), [])

    def test_unrelated_saves_do_not_reindex(self):
        with mock.patch('forum.search.index.index_question') as index:
            question = Question.objects.get(id=self.csv.id)
            question.score = 5
            question.save()
        index.assert_not_called()

    def test_rebuild_command(self):
        SearchDocument.objects.all().delete()
        Question.objects.filter(id=self.body_only.id).update(state_string='(deleted)')
        out = StringIO()
        call_command('rebuild_search_index', stdout=out)
        self.assertIn('Indexed 2 questions for search (sqlite-fts5).', out.getvalue())
        self.assertEqual(set(SearchDocument.objects.values_list('question_id', flat=True)),
                         {self.csv.id, self.title_match.id})
        self.assertEqual(self.ids('raster'), [self.title_match.id])
        self.assertEqual(self.ids('backup'), [self.csv.id])

    def test_rebuild_creates_missing_fts_table(self):
        # As after a migration that made Django rebuild the table (triggers lost).
        with connection.cursor() as cursor:
            for trigger in ('ai', 'ad', 'au'):
                cursor.execute('DROP TRIGGER forum_searchdocument_%s' % trigger)
            cursor.execute('DROP TABLE forum_searchdocument_fts')
        backends.reset()
        self.assertEqual(backends.get_backend().name, 'keywords')
        call_command('rebuild_search_index', stdout=StringIO())
        self.assertEqual(backends.get_backend().name, 'sqlite-fts5')
        self.assertEqual(self.ids('raster'), [self.title_match.id, self.body_only.id])


class FallbackTest(SearchFixture):
    def test_keyword_matching_without_fts5(self):
        with mock.patch('forum.search.backends.sqlite_fts_available', return_value=False):
            self.assertEqual(backends.get_backend().name, 'keywords')
            self.assertEqual(self.ids('raster')[:2], [self.title_match.id, self.body_only.id])
            self.assertEqual(self.ids('"survey results"'), [self.body_only.id])
            self.assertEqual(self.ids('export -spreadsheet [collect]'), [self.body_only.id])

    def test_backend_failure_falls_back(self):
        with mock.patch.object(backends.SQLiteBackend, 'rank', side_effect=RuntimeError('broken index')), \
                self.assertLogs('forum.search', 'ERROR'):
            self.assertEqual(self.ids('backup'), [self.csv.id])

    def test_before_the_index_is_built(self):
        SearchDocument.objects.all().delete()
        self.assertFalse(engine.index_ready())
        self.assertEqual(set(self.ids('raster')), {self.title_match.id, self.body_only.id})
        response = self.client.get('/search/', {'q': 'raster'})
        self.assertContains(response, '<mark>Raster</mark> layers in Earth Map')

    def test_search_questions_providers(self):
        from forum.utils import similar
        self.assertEqual(similar.SEARCH_PROVIDERS[0], similar.full_text_questions)
        self.assertEqual([q.id for q in similar.search_questions('raster')][:1], [self.title_match.id])
        # A second provider's results are blended in.
        with mock.patch.object(similar, 'SEARCH_PROVIDERS',
                               [similar.full_text_questions, lambda q, limit: [self.csv, self.body_only]]):
            found = [q.id for q in similar.search_questions('raster')]
        self.assertEqual(found[0], self.body_only.id)
        self.assertEqual(set(found), {self.body_only.id, self.title_match.id, self.csv.id})

    def test_mcp_search(self):
        response = self.client.post('/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'search_forum', 'arguments': {'query': 'raster'}}}), content_type='application/json')
        hits = json.loads(response.json()['result']['content'][0]['text'])['results']
        self.assertEqual([h['id'] for h in hits], [self.title_match.id, self.body_only.id])


class SnippetTest(OSQATestCase):
    def test_marks_only_terms_and_escapes_everything_else(self):
        text = html_to_text('<p>Run <code>&lt;script&gt;alert("x")&lt;/script&gt;</code> to export <b>data</b></p>')
        self.assertEqual(text, 'Run <script>alert("x")</script> to export data')
        result = snippet(highlighter(parse('export script').highlight_terms()), text)
        self.assertEqual(result, 'Run &lt;<mark>script</mark>&gt;alert(&quot;x&quot;)&lt;/<mark>script</mark>&gt; '
                                 'to <mark>export</mark> data')

    def test_phrases_endings_and_window(self):
        regex = highlighter(parse('"cloud mask" exporting').highlight_terms())
        text = 'Start. ' + 'filler words here ' * 30 + 'Masking clouds: apply the Cloud  Mask and export it.' + \
            ' more' * 100
        result = snippet(regex, '', text)
        self.assertTrue(result.startswith('… '))
        self.assertTrue(result.endswith(' …'))
        self.assertIn('<mark>Cloud  Mask</mark>', result)
        self.assertIn('<mark>export</mark>', result)
        self.assertLess(len(result), 320)

    def test_no_match_uses_the_start(self):
        self.assertEqual(snippet(highlighter([['zzz']]), '<b>plain</b> & text'), '&lt;b&gt;plain&lt;/b&gt; &amp; text')


class SearchPageTest(SearchFixture):
    def test_results_page(self):
        evil = self.ask(self.bob, 'Raster with script', 'collect',
                        'Type <code>&lt;script&gt;alert(1)&lt;/script&gt;</code> in the raster field.')
        response = self.client.get('/search/', {'q': 'raster'})
        self.assertCleanPage(response)
        page = response.content.decode()
        self.assertIn('About 3 results', page)
        self.assertLess(page.index('<mark>Raster</mark> layers in Earth Map'), page.index('Exporting survey results'))
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; in the <mark>raster</mark> field.', page)
        self.assertNotIn('<script>alert(1)', page)
        self.assertIn('class="tabsA"', page)
        self.assertIn('?q=raster&amp;sort=votes', page)
        self.assertIn('Search tips', page)
        self.assertIn(evil.get_absolute_url(), page)

    def test_accepted_marker_and_counts(self):
        page = self.client.get('/search/', {'q': 'csv'}).content.decode()
        self.assertIn('answered-accepted', page)
        self.assertIn('this question has an accepted answer', page)

    def test_sort_tabs(self):
        Question.objects.filter(id=self.body_only.id).update(score=10)
        page = self.client.get('/search/', {'q': 'raster', 'sort': 'votes'}).content.decode()
        self.assertLess(page.index('Exporting survey results'), page.index('<mark>Raster</mark> layers in Earth Map'))
        page = self.client.get('/search/', {'q': '[collect]'}).content.decode()
        self.assertNotIn('sort=relevance', page)  # nothing to rank by

    def test_empty_state(self):
        page = self.client.get('/search/', {'q': 'raster [collect] is:accepted'}).content.decode()
        self.assertIn('No questions match your search.', page)
        self.assertIn('href="/search/?q=raster"', page)
        self.assertIn('href="/questions/ask/?title=raster"', page)
        for q in ('zebra', '[nosuchtag]'):
            response = self.client.get('/search/', {'q': q})
            self.assertCleanPage(response)
            self.assertContains(response, 'No results')
            self.assertContains(response, 'No questions match your search.')
        ask = self.client.get('/questions/ask/', {'title': 'raster'}).content.decode()
        self.assertIn('value="raster"', ask)

    def test_search_page_without_query(self):
        response = self.client.get('/search/')
        self.assertCleanPage(response)
        self.assertContains(response, 'id="search-page-q"')
        self.assertContains(response, '<details class="search-tips" open>')
        self.assertNotContains(response, 'cse.google.com')

    def test_redirects_and_feed(self):
        self.assertRedirects(self.client.get('/search/', {'q': 'gis', 't': 'user'}), '/users/?q=gis',
                             fetch_redirect_response=False)
        feed = self.client.get('/search/', {'q': 'raster', 'type': 'rss'})
        self.assertEqual(feed.status_code, 200)
        self.assertIn('Raster layers in Earth Map', feed.content.decode())
