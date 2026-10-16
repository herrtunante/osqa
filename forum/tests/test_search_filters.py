"""Search page filters (forum/search/filters.py), facets, "did you mean",
chips and URLs, the MCP search_forum filter parameters and query budgets."""
import datetime
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext

from forum.actions import AcceptAnswerAction, DeleteAction
from forum.models import Answer, Question, SearchDocument, Tag, TagSynonym
from forum.search import backends, engine, spelling
from forum.search.filters import SearchFilters
from forum.search.query import parse

from .base import OSQATestCase
from .test_query_budgets import QueryBudgetMixin

CODE = '<p>Try this:</p><pre><code>import csv\nrows = list(csv.reader(f))</code></pre>'


def days_ago(days):
    return datetime.datetime.now() - datetime.timedelta(days=days)


class FilterFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        backends.reset()
        self.addCleanup(backends.reset)
        self.alice = self.create_user('alice')
        self.bob = self.create_user('Bob')
        self.newbie = self.create_user('newbie')
        # Python + CSV, code in the question, an accepted answer, 3 days old, score 5.
        self.py_csv = self.ask(self.alice, 'Reading CSV files in Python', 'python csv', CODE, days=3, score=5)
        accepted = self.answer(self.bob, self.py_csv, 'Use the csv module, it handles the quoting.')
        AcceptAnswerAction(user=self.alice, node=accepted, ip='127.0.0.1').save()
        # Python, answered (no accepted answer) and the code is in the answer; 20 days old.
        self.py_json = self.ask(self.bob, 'Parsing JSON in Python', 'python json', 'How do I parse a JSON file?',
                                days=20, score=1)
        self.answer(self.alice, self.py_json, 'Call json.load:<pre><code>json.load(f)</code></pre>')
        # Unanswered, 200 days old, closed.
        self.r_csv = self.ask(self.alice, 'Writing CSV files from R', 'r csv', 'Which function writes a CSV file?',
                              days=200, score=0)
        Question.objects.filter(id=self.r_csv.id).update(state_string='(closed)')
        # Unanswered, 400 days old, negative score; its only answer is held for review (with code).
        self.old = self.ask(self.bob, 'Exporting CSV from an old Excel version', 'excel csv',
                            'An old spreadsheet question about CSV.', days=400, score=-2)
        held = self.answer(self.newbie, self.old, 'Held answer<pre><code>held()</code></pre>')
        held.state_string = '(pending_review)'
        held.save()
        # Held and deleted questions never show up, whatever the filters.
        self.held = self.ask(self.newbie, 'Held CSV question in Python', 'python csv', CODE)
        self.held.state_string = '(pending_review)'
        self.held.save()
        self.deleted = self.ask(self.alice, 'Deleted CSV question in Python', 'python csv', CODE)
        DeleteAction(user=self.alice, node=self.deleted, ip='127.0.0.1').save()

    def ask(self, author, title, tags, body, days=0, score=0):
        question = Question(author=author, title=title, tagnames=tags, body=body)
        question.save()
        Question.objects.filter(id=question.id).update(added_at=days_ago(days), last_activity_at=days_ago(days),
                                                       score=score)
        return question

    def answer(self, author, question, body):
        answer = Answer(author=author, parent=question, body=body)
        answer.save()
        return answer

    def ids(self, text='', **params):
        query = QueryDict(mutable=True)
        for key, value in params.items():
            query.setlist(key, value if isinstance(value, list) else [value])
        questions, ranked = engine.search(parse(text), filters=SearchFilters.from_params(query))
        return set(questions.values_list('id', flat=True))

    def page(self, **params):
        response = self.client.get('/search/', params)
        self.assertCleanPage(response)
        return response.content.decode()


class FilterParsingTest(OSQATestCase):
    def test_from_params(self):
        filters = SearchFilters.from_params(QueryDict(
            'tags=Python+csv&tags=json,[gis]&status=accepted&closed=hide&date=custom&from=2026-01-01&to=bad'
            '&author=+alice+&code=1&min_score=3'))
        self.assertEqual(filters.tags, ['python', 'csv', 'json', 'gis'])
        self.assertEqual((filters.status, filters.hide_closed, filters.date), ('accepted', True, 'custom'))
        self.assertEqual((filters.date_from, filters.date_to), (datetime.date(2026, 1, 1), None))
        self.assertEqual((filters.author, filters.has_code, filters.min_score), ('alice', True, 3))
        self.assertEqual(filters.active_count, 10)
        self.assertEqual(filters.params(), [('tags', 'python csv json gis'), ('status', 'accepted'),
                                            ('closed', 'hide'), ('date', 'custom'), ('from', '2026-01-01'),
                                            ('author', 'alice'), ('code', '1'), ('min_score', '3')])

    def test_bad_values_are_ignored(self):
        filters = SearchFilters.from_params(QueryDict('status=nonsense&date=decade&min_score=lots&code=no&closed=x'
                                                      '&tags=a+b+c+d+e+f+g'))
        self.assertEqual(filters.params(), [('tags', 'a b c d e')])
        self.assertTrue(SearchFilters.from_params({}).is_empty)
        # A range without date=custom still applies; date=week drops it.
        self.assertEqual(SearchFilters.from_params({'from': '2026-02-03'}).date, 'custom')
        self.assertIsNone(SearchFilters.from_params({'date': 'week', 'from': '2026-02-03'}).date_from)
        self.assertEqual(SearchFilters.from_params({'date': 'custom'}).date, '')

    def test_without(self):
        filters = SearchFilters(tags=['a', 'b'], status='answered', min_score=0)
        self.assertEqual(filters.without('tags', 'a').tags, ['b'])
        self.assertEqual(filters.without('status').params(), [('tags', 'a b'), ('min_score', '0')])
        self.assertEqual(filters.without('min_score').min_score, None)

    def test_operator_spans(self):
        query = parse('csv [python] export user:bob is:answered tag:"r" -excel is:nonsense')
        self.assertEqual([(k, v) for k, v, s, e in query.operators],
                         [('tag', 'python'), ('user', 'bob'), ('is', 'answered'), ('tag', 'r')])
        self.assertEqual(query.without_operators(), 'csv export -excel is:nonsense')
        self.assertEqual(query.without_operators(1), 'csv [python] export is:answered tag:"r" -excel is:nonsense')


class FiltersTest(FilterFixture):
    def test_tags(self):
        self.assertEqual(self.ids(tags='python'), {self.py_csv.id, self.py_json.id})
        self.assertEqual(self.ids(tags='python csv'), {self.py_csv.id})
        self.assertEqual(self.ids(tags=['PYTHON', 'csv']), {self.py_csv.id})
        self.assertEqual(self.ids(tags='nosuchtag'), set())
        TagSynonym.objects.create(source_name='py', target=Tag.objects.get(name='python'))
        self.assertEqual(self.ids(tags='py'), {self.py_csv.id, self.py_json.id})
        # With the query's own [tag] operator and text.
        self.assertEqual(self.ids('csv [csv]', tags='python'), {self.py_csv.id})

    def test_status(self):
        self.assertEqual(self.ids(status='accepted'), {self.py_csv.id})
        self.assertEqual(self.ids(status='answered'), {self.py_csv.id, self.py_json.id})
        # The held answer doesn't count.
        self.assertEqual(self.ids(status='unanswered'), {self.r_csv.id, self.old.id})
        self.assertEqual(self.ids('csv is:answered', status='unanswered'), set())
        self.assertEqual(self.ids('csv is:answered', status='accepted'), {self.py_csv.id})

    def test_closed(self):
        self.assertIn(self.r_csv.id, self.ids('csv'))
        self.assertEqual(self.ids('csv', closed='hide'), {self.py_csv.id, self.old.id})

    def test_dates(self):
        self.assertEqual(self.ids(date='week'), {self.py_csv.id})
        self.assertEqual(self.ids(date='month'), {self.py_csv.id, self.py_json.id})
        self.assertEqual(self.ids(date='year'), {self.py_csv.id, self.py_json.id, self.r_csv.id})
        start, end = days_ago(250).date().isoformat(), days_ago(10).date().isoformat()
        self.assertEqual(self.ids(date='custom', **{'from': start, 'to': end}), {self.py_json.id, self.r_csv.id})
        self.assertEqual(self.ids(**{'to': days_ago(300).date().isoformat()}), {self.old.id})
        self.assertEqual(len(self.ids(date='custom', **{'from': 'yesterday'})), 4)  # bad date: no filter

    def test_author(self):
        self.assertEqual(self.ids(author='bob'), {self.py_json.id, self.old.id})
        self.assertEqual(self.ids('user:alice', author='bob'), set())
        self.assertEqual(self.ids('json', author='BOB'), {self.py_json.id})
        self.assertEqual(self.ids(author='newbie'), set())  # only held posts

    def test_code(self):
        # In the question, or in a visible answer; not in a held answer.
        self.assertEqual(self.ids(code='1'), {self.py_csv.id, self.py_json.id})

    def test_min_score(self):
        self.assertEqual(self.ids(min_score='1'), {self.py_csv.id, self.py_json.id})
        self.assertEqual(self.ids(min_score='-5'), {self.py_csv.id, self.py_json.id, self.r_csv.id, self.old.id})

    def test_combined_with_text_and_operators(self):
        self.assertEqual(self.ids('csv -excel', tags='csv', status='unanswered'), {self.r_csv.id})
        self.assertEqual(self.ids('"csv files"', date='year', closed='hide'), {self.py_csv.id})
        self.assertEqual(self.ids('python', code='1', min_score='2', author='alice'), {self.py_csv.id})
        # Only filters, no words: every public question that passes.
        self.assertEqual(self.ids('', tags='csv', status='unanswered', closed='hide'), {self.old.id})

    def test_held_and_deleted_never_match(self):
        for params in ({}, {'tags': 'python csv'}, {'code': '1'}, {'author': 'newbie'}, {'date': 'week'}):
            found = self.ids('csv python', **params)
            self.assertNotIn(self.held.id, found)
            self.assertNotIn(self.deleted.id, found)
        page = self.page(q='held', tags='python')
        self.assertNotIn('Held CSV question', page)

    def test_keyword_fallback(self):
        """Without FTS5 (as with the LIKE matching used on other databases)."""
        with mock.patch('forum.search.backends.sqlite_fts_available', return_value=False):
            self.assertEqual(backends.get_backend().name, 'keywords')
            self.assertEqual(self.ids('csv', tags='python'), {self.py_csv.id})
            self.assertEqual(self.ids('csv -excel', status='unanswered'), {self.r_csv.id})
            self.assertEqual(self.ids('csv', code='1', date='month'), {self.py_csv.id})
            page = self.page(q='csv', status='accepted')
            self.assertIn('Reading <mark>CSV</mark> files in Python', page)

    def test_before_the_index_is_built(self):
        SearchDocument.objects.all().delete()
        self.assertEqual(self.ids('csv', tags='python', code='1'), {self.py_csv.id})

    def test_filters_apply_to_any_backends_ids(self):
        """MySQL FULLTEXT and Postgres rank ids like FTS5: the filters are SQL on top."""
        everything = [self.old.id, self.r_csv.id, self.py_json.id, self.py_csv.id, self.held.id]
        with mock.patch.object(backends, 'rank', return_value=everything) as rank:
            questions, ranked = engine.search(parse('csv'), filters=SearchFilters(tags=['python']))
            self.assertEqual(list(questions.order_by('search_rank').values_list('id', flat=True)),
                             [self.py_json.id, self.py_csv.id])
        rank.assert_called_once()


class FacetsTest(FilterFixture):
    def test_status_and_tag_facets(self):
        questions, ranked = engine.search(parse('csv'))
        rows = engine.matched_rows(questions)
        counts = engine.status_facets(rows)
        self.assertEqual({k: counts[k] for k in ('total', 'answered', 'accepted', 'unanswered', 'closed')},
                         {'total': 3, 'answered': 1, 'accepted': 1, 'unanswered': 2, 'closed': 1})
        self.assertEqual(engine.tag_facets([r[0] for r in rows], leave_out={'csv'}),
                         [('excel', 1), ('python', 1), ('r', 1)])

    def test_facets_on_the_page(self):
        page = self.page(q='python', status='accepted')
        # Status counts leave the status filter out, so the other choices show what they would give.
        self.assertRegex(page, r'value="answered"[^>]*/>\s*<span>Answered</span> <span class="search-facet-count">2<')
        self.assertRegex(page, r'value="accepted"[^>]*checked />\s*<span>Accepted answer</span> '
                               r'<span class="search-facet-count">1<')
        # Tags of the results (python itself left out when filtered).
        self.assertIn('href="/search/?q=python&amp;tags=csv&amp;status=accepted"', page)
        self.assertNotIn('?q=python&amp;tags=json', page)  # json isn't in the accepted results

    def test_facets_are_capped(self):
        with mock.patch.object(engine, 'FACET_CAP', 2):
            counts = engine.status_facets(engine.matched_rows(engine.search(parse('csv'))[0], cap=2))
        self.assertTrue(counts['capped'])
        self.assertEqual(counts['total'], 2)


class DidYouMeanTest(FilterFixture):
    def test_misspelt_word(self):
        self.assertEqual(spelling.suggest(parse('pyhton')), 'python')
        self.assertEqual(spelling.suggest(parse('reading pyhton files')), 'reading python files')
        self.assertIsNone(spelling.suggest(parse('python')))
        page = self.page(q='pyhton')
        self.assertIn('Did you mean:', page)
        self.assertIn('<a href="/search/?q=python"><strong>python</strong></a>', page)

    def test_misspelt_tag_keeps_filters(self):
        self.assertEqual(spelling.suggest(parse('csv [pyhton]')), 'csv [python]')
        page = self.page(q='[pyhton]', date='month')
        self.assertIn('href="/search/?q=%5Bpython%5D&amp;date=month"', page)

    def test_only_when_it_finds_more(self):
        self.assertNotIn('Did you mean', self.page(q='csv'))
        # "zzzzzz" is like nothing; a close word with no more results isn't offered either.
        self.assertNotIn('Did you mean', self.page(q='zzzzzz'))
        self.assertNotIn('Did you mean', self.page(q='pyhton', author='nobody'))

    def test_vocabulary_is_cached(self):
        spelling.vocabulary()
        with CaptureQueriesContext(connection) as ctx:
            spelling.vocabulary()
        self.assertEqual(len(ctx.captured_queries), 0)
        cache.clear()
        self.assertIn('python', spelling.vocabulary()['tags'])


class SearchPageFiltersTest(FilterFixture):
    def test_chips_and_clear_all(self):
        page = self.page(q='csv [python] is:answered', tags='csv', code='1', sort='votes')
        self.assertIn('<span class="visually-hidden">Remove filter: </span>Tag: python', page)
        # Removing the [python] operator keeps the other filters and the sort.
        self.assertIn('href="/search/?q=csv+is%3Aanswered&amp;tags=csv&amp;code=1&amp;sort=votes"', page)
        self.assertIn('href="/search/?q=csv+%5Bpython%5D+is%3Aanswered&amp;code=1&amp;sort=votes"', page)  # tag chip
        self.assertIn('class="search-chips-clear" href="/search/?q=csv&amp;sort=votes"', page)
        self.assertIn('<input type="hidden" name="sort" value="votes" />', page)
        self.assertIn('<span class="search-filters-count" aria-hidden="true">2</span>', page)
        self.assertIn('<p class="search-count" role="status">1 result</p>', page)

    def test_form_shows_the_filters(self):
        page = self.page(tags='python', status='answered', closed='hide', date='custom', **{'from': '2026-01-02'},
                         author='Bob', code='1', min_score='1')
        self.assertIn('name="tags" form="search-form" value="python"', page)
        self.assertRegex(page, r'value="answered" form="search-form" autocomplete="off" checked')
        self.assertIn('value="hide" form="search-form" autocomplete="off" checked', page)
        self.assertIn('<option value="custom" selected>', page)
        self.assertIn('name="from" form="search-form" autocomplete="off" value="2026-01-02"', page)
        self.assertIn('name="author" form="search-form" value="Bob"', page)
        self.assertIn('name="min_score" form="search-form" autocomplete="off" inputmode="numeric" step="1" value="1"',
                      page)
        self.assertIn('Asked since 2026-01-02', page)

    def test_results_markup(self):
        page = self.page(q='csv')
        self.assertIn('class="short-summary question-row search-result is-accepted"', page)
        self.assertIn('<span class="search-status search-status-accepted">Accepted</span>', page)
        self.assertIn('<span class="search-status search-status-closed">Closed</span>', page)
        self.assertIn('Reading <mark>CSV</mark> files in Python', page)
        self.assertIn('by <a href="%s">alice</a>' % self.alice.get_profile_url(), page)
        self.assertIn('<time class="relative-time"', page)

    def test_titles_are_escaped(self):
        self.ask(self.alice, 'CSV <script>alert(1)</script> & co', 'csv', 'A question about <b>csv</b>.')
        page = self.page(q='csv script')
        self.assertIn('<mark>CSV</mark> &lt;<mark>script</mark>&gt;alert(1)&lt;/<mark>script</mark>&gt; &amp; co', page)
        self.assertNotIn('<script>alert(1)', page)

    def test_filters_without_words(self):
        page = self.page(tags='python')
        self.assertIn('About 2 results', page)
        self.assertIn('Parsing JSON in Python', page)
        self.assertNotIn('sort=relevance', page)

    def test_empty_state(self):
        page = self.page(q='csv [python] is:unanswered', min_score='3')
        self.assertIn('No questions match your search.', page)
        self.assertIn('No results', page)
        self.assertIn('href="/search/?q=csv"', page)
        self.assertIn('<details class="search-tips" open>', page)
        page = self.page(tags='nosuchtag')
        self.assertIn('Remove some of the filters', page)

    def test_pagination_keeps_filters(self):
        for i in range(17):
            self.ask(self.alice, 'Python question number %d' % i, 'python', 'Body of question %d' % i)
        page = self.page(q='python', tags='python', closed='hide', pagesize='15')
        self.assertIn('/search/?q=python&amp;tags=python&amp;closed=hide&amp;sort=relevance&amp;page=2', page)
        response = self.client.get('/search/', {'q': 'python', 'tags': 'python', 'closed': 'hide', 'page': '2'})
        self.assertContains(response, '<mark>Python</mark> question number')

    def test_rss_feed_uses_filters(self):
        feed = self.client.get('/search/', {'q': 'csv', 'status': 'unanswered', 'type': 'rss'}).content.decode()
        self.assertIn('Writing CSV files from R', feed)
        self.assertNotIn('Reading CSV files in Python', feed)
        page = self.page(q='csv', status='unanswered')
        self.assertIn('href="/search/?q=csv&amp;status=unanswered&amp;type=rss"', page)

    def test_empty_search_page(self):
        page = self.page()
        self.assertIn('id="search-filters"', page)
        self.assertNotIn('search-count', page)
        self.assertIn('<details class="search-tips" open>', page)

    def test_chip_urls_round_trip(self):
        page = self.page(q='csv', tags='python csv', date='week')
        for href in ('/search/?q=csv&amp;tags=csv&amp;date=week', '/search/?q=csv&amp;tags=python&amp;date=week',
                     '/search/?q=csv&amp;tags=python+csv'):
            self.assertIn('href="%s"' % href, page)
            params = parse_qs(urlsplit(href.replace('&amp;', '&')).query)
            self.assertTrue(SearchFilters.from_params(params).active_count >= 1)


class MCPFiltersTest(FilterFixture):
    def rpc(self, arguments):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'search_forum', 'arguments': arguments}})
        return self.client.post('/mcp', body, content_type='application/json').json()['result']

    def hits(self, **arguments):
        result = self.rpc(dict(arguments, query=arguments.pop('query', 'csv')))
        self.assertFalse(result['isError'], result)
        return [hit['id'] for hit in json.loads(result['content'][0]['text'])['results']]

    def test_schema(self):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        tools = self.client.post('/mcp', body, content_type='application/json').json()['result']['tools']
        properties = tools[0]['inputSchema']['properties']
        self.assertEqual(properties['status']['enum'], ['unanswered', 'answered', 'accepted'])
        self.assertEqual(properties['tags']['type'], 'array')
        self.assertEqual(properties['since']['format'], 'date')

    def test_filters(self):
        self.assertEqual(set(self.hits()), {self.py_csv.id, self.r_csv.id, self.old.id})
        self.assertEqual(self.hits(tags=['python']), [self.py_csv.id])
        self.assertEqual(self.hits(tags='excel'), [self.old.id])
        self.assertEqual(set(self.hits(status='unanswered')), {self.r_csv.id, self.old.id})
        self.assertEqual(self.hits(status='accepted', query='csv python'), [self.py_csv.id])
        self.assertEqual(set(self.hits(since=days_ago(250).date().isoformat())), {self.py_csv.id, self.r_csv.id})

    def test_bad_arguments(self):
        self.assertTrue(self.rpc({'query': 'csv', 'status': 'solved'})['isError'])
        self.assertTrue(self.rpc({'query': 'csv', 'since': 'last week'})['isError'])
        self.assertTrue(self.rpc({'query': 'csv', 'tags': [1, 2]})['isError'])

    def test_other_providers_are_filtered_after(self):
        from forum.utils import similar
        semantic = lambda query, limit: [self.r_csv, self.py_json]  # noqa: E731 - no filters argument
        with mock.patch.object(similar, 'SEARCH_PROVIDERS', [similar.full_text_questions, semantic]):
            found = similar.search_questions('csv', 10, SearchFilters(tags=['python']))
            self.assertEqual({q.id for q in found}, {self.py_csv.id, self.py_json.id})
            # The keyword fallback too.
            with mock.patch.object(similar, 'SEARCH_PROVIDERS', []):
                found = similar.search_questions('Writing CSV', 10, SearchFilters(status='unanswered'))
                self.assertEqual([q.id for q in found], [self.r_csv.id, self.old.id])

    def test_llms_txt_describes_the_search(self):
        text = self.client.get('/llms.txt').content.decode()
        self.assertIn('/search/?q=', text)
        self.assertIn('status=unanswered|answered|accepted', text)


class SearchQueryBudgetTest(QueryBudgetMixin, FilterFixture):
    def setUp(self):
        super().setUp()
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_questions(self, count):
        start = Question.objects.count()
        for i in range(start, start + count):
            question = self.ask(self.create_user('author%d' % i), 'Python CSV question %d' % i, 'python csv',
                                'Body %d <pre><code>x = %d</code></pre>' % (i, i))
            self.answer(self.alice, question, 'An answer to question %d' % i)

    def test_filtered_search_page(self):
        url = '/search/?q=csv+python&tags=python&status=answered&closed=hide&date=year&code=1&min_score=0'
        self.add_questions(5)
        # Measured 20/17, 19/15, 16/12: results (rank, count, page, documents,
        # answer counts), facets (matched ids, status, tags; the status facet
        # searches once more without the status filter), the tag lookup, session.
        self.assertQueryBudget(url, cold=22, warm=19)
        self.assertQueryBudget('/search/?q=csv&tags=csv', cold=21, warm=17)
        self.assertQueryBudget('/search/?tags=python&sort=newest', cold=18, warm=14)

    def test_no_query_per_result(self):
        url = '/search/?q=csv+python&tags=python&status=answered&code=1'
        self.add_questions(3)
        self.client.get(url)
        cache.clear()
        few = self.count_queries('get', url)[0]
        self.add_questions(12)
        self.client.get(url)
        cache.clear()
        many = self.count_queries('get', url)[0]
        self.assertEqual(few, many)
