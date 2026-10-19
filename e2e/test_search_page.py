"""The search page's filters: the sidebar on a desktop (applied at once,
kept in the URL, back button), chips and "Clear all", tag and author
suggestions, the custom date range, the "Filters" dialog on a phone in dark
mode, facet links, "did you mean", the empty state, pagination keeping the
filters, j / k on the results, and axe on all of it."""
from urllib.parse import parse_qs, urlsplit

from forum.models import Question

from . import test_accessibility as a11y
from .base import E2ETestCase


def params(page):
    return {key: values[-1] for key, values in parse_qs(urlsplit(page.url).query).items()}


class SearchPageTest(E2ETestCase):
    axe = a11y.AccessibilityTest.axe

    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bobby', reputation=200)
        self.py_csv = self.create_question(self.alice, 'Reading CSV files in Python', tags='python csv',
                                           body='How do I read a CSV file?\n\n```\nimport csv\n```', days_ago=3)
        self.create_answer(self.bob, self.py_csv, 'Use the csv module from the standard library, it is enough.')
        self.r_csv = self.create_question(self.bob, 'Writing CSV files from R', tags='r csv',
                                          body='Which R function writes a CSV file with a header?', days_ago=200)
        self.excel = self.create_question(self.alice, 'Exporting CSV from Excel', tags='excel csv',
                                          body='Excel writes a CSV file with semicolons, how do I change it?')

    def assert_axe_clean(self, page, where):
        violations = self.axe(page)
        self.assertEqual(violations, [], '%s:\n%s' % (where, '\n'.join(violations)))

    def results(self, page):
        return page.locator('#search-results h2 a').all_inner_texts()

    def submitted(self, page, action):
        with page.expect_navigation():
            action()
        page.wait_for_load_state()
        self.check_page(page)

    def test_desktop_filters_chips_url_and_back(self):
        page = self.open()
        self.visit(page, '/search/?q=csv')
        self.assertTrue(page.locator('#search-filters').is_visible())
        self.assertFalse(page.locator('.search-filters-toggle').is_visible())
        self.assertEqual(len(self.results(page)), 3)
        self.assertIn('About 3 results', page.locator('.search-count').inner_text())
        # Facet counts: 1 answered, 2 unanswered.
        self.assertEqual(page.locator('label:has(input[value="unanswered"]) .search-facet-count').inner_text(), '2')

        # A radio button applies at once; the URL holds it.
        self.submitted(page, lambda: page.get_by_label('Unanswered').check())
        self.assertEqual(params(page), {'q': 'csv', 'status': 'unanswered'})
        self.assertEqual(set(self.results(page)), {'Writing CSV files from R', 'Exporting CSV from Excel'})
        self.assertEqual(page.locator('.search-chip').count(), 1)
        self.assertIn('Unanswered', page.locator('.search-chip').inner_text())

        # The date select too.
        self.submitted(page, lambda: page.select_option('#search-filter-date', 'month'))
        self.assertEqual(params(page), {'q': 'csv', 'status': 'unanswered', 'date': 'month'})
        self.assertEqual(self.results(page), ['Exporting CSV from Excel'])
        self.assertEqual(page.locator('.search-filters-count').count(), 1)

        # Back: the previous filters, in the URL and in the form.
        with page.expect_navigation():
            page.go_back()
        self.check_page(page)
        self.assertEqual(params(page), {'q': 'csv', 'status': 'unanswered'})
        self.assertEqual(page.locator('#search-filter-date').input_value(), '')
        self.assertTrue(page.get_by_label('Unanswered').is_checked())

        # A tag from the suggestions, then Enter.
        page.fill('#search-filter-tags', 'exc')
        page.locator('.osqa-autocomplete li', has_text='excel').first.click()
        self.assertEqual(page.locator('#search-filter-tags').input_value(), 'excel ')
        self.submitted(page, lambda: page.locator('#search-filter-tags').press('Enter'))
        self.assertEqual(params(page), {'q': 'csv', 'status': 'unanswered', 'tags': 'excel'})
        self.assertEqual(self.results(page), ['Exporting CSV from Excel'])

        # A chip removes its filter only; "Clear all" leaves the words.
        self.submitted(page, lambda: page.locator('.search-chip', has_text='Unanswered').click())
        self.assertEqual(params(page), {'q': 'csv', 'tags': 'excel'})
        self.submitted(page, lambda: page.locator('.search-toolbar .search-chips-clear').click())
        self.assertEqual(params(page), {'q': 'csv'})
        self.assertEqual(page.locator('.search-chip').count(), 0)

        # The query's own operators get chips too.
        self.submitted(page, lambda: (page.fill('#search-page-q', 'csv [r]'), page.press('#search-page-q', 'Enter')))
        self.assertEqual(self.results(page), ['Writing CSV files from R'])
        self.submitted(page, lambda: page.locator('.search-chip', has_text='Tag: r').click())
        self.assertEqual(params(page), {'q': 'csv'})

        # A tag of the results' facets adds it.
        self.submitted(page, lambda: page.locator('.search-facet-tags a', has_text='python').click())
        self.assertEqual(params(page), {'q': 'csv', 'tags': 'python'})
        self.assert_axe_clean(page, 'search page with a tag filter (desktop)')

    def test_author_score_code_and_custom_dates(self):
        page = self.open()
        self.visit(page, '/search/?q=csv&sort=newest')
        page.fill('#search-filter-author', 'bob')
        page.locator('.osqa-autocomplete li', has_text='bobby').first.click()
        page.wait_for_load_state()
        self.check_page(page)
        self.assertEqual(params(page), {'q': 'csv', 'author': 'bobby', 'sort': 'newest'})
        self.assertEqual(self.results(page), ['Writing CSV files from R'])
        self.submitted(page, lambda: page.locator('.search-chip', has_text='Asked by bobby').click())

        # Custom range: its fields show when chosen, and apply with the button.
        self.assertFalse(page.locator('#search-filter-from').is_visible())
        page.select_option('#search-filter-date', 'custom')
        self.assertTrue(page.locator('#search-filter-from').is_visible())
        page.fill('#search-filter-from', '2000-01-01')
        page.fill('#search-filter-score', '0')
        self.submitted(page, lambda: page.get_by_role('button', name='Apply filters').click())
        self.assertEqual(params(page), {'q': 'csv', 'date': 'custom', 'from': '2000-01-01', 'min_score': '0',
                                        'sort': 'newest'})
        self.assertEqual(len(self.results(page)), 3)
        self.assertIn('Asked since 2000-01-01', page.locator('.search-chips').inner_text())

        self.submitted(page, lambda: page.get_by_label('Has code blocks').check())
        self.assertEqual(self.results(page), ['Reading CSV files in Python'])
        self.assertEqual(params(page)['code'], '1')

    def test_phone_dialog_in_dark_mode(self):
        page = self.open(device='mobile', dark=True)
        self.visit(page, '/search/?q=csv&tags=csv')
        self.assertFalse(page.locator('#search-filters').is_visible())
        toggle = page.locator('.search-filters-toggle')
        self.assertTrue(toggle.is_visible())
        self.assertEqual(toggle.locator('.search-filters-count').inner_text(), '1')
        self.assertIn('(1 active)', toggle.inner_text())
        toggle.click()
        dialog = page.locator('#search-filters-dialog')
        self.assertTrue(dialog.evaluate('d => d.open'))
        self.assertTrue(page.locator('#search-filters-dialog #search-filters').is_visible())
        self.assertNotEqual(dialog.evaluate('d => getComputedStyle(d).backgroundColor'), 'rgb(255, 255, 255)')
        self.check_page(page)
        self.assert_axe_clean(page, 'filters dialog (phone, dark)')

        # In the dialog a choice waits for "Apply filters".
        page.get_by_label('Unanswered').check()
        page.wait_for_timeout(300)
        self.assertEqual(params(page), {'q': 'csv', 'tags': 'csv'})
        # The tag suggestions show above the dialog.
        page.fill('#search-filter-tags', 'csv exc')
        option = page.locator('.osqa-autocomplete li', has_text='excel').first
        option.wait_for(state='visible')
        self.assertEqual(page.evaluate('() => !!document.querySelector("#search-filters-dialog .osqa-autocomplete")'),
                         True)
        option.click()
        self.submitted(page, lambda: page.get_by_role('button', name='Apply filters').click())
        self.assertEqual(params(page), {'q': 'csv', 'tags': 'csv excel', 'status': 'unanswered'})
        self.assertEqual(self.results(page), ['Exporting CSV from Excel'])
        self.assertEqual(page.locator('.search-filters-count').inner_text(), '3')

        # Escape closes the dialog and gives the focus back to the button.
        page.locator('.search-filters-toggle').click()
        page.keyboard.press('Escape')
        self.assertFalse(dialog.evaluate('d => d.open'))
        self.assertTrue(page.evaluate('() => document.activeElement.classList.contains("search-filters-toggle")'))
        self.assertEqual(page.locator('#search-filters-home #search-filters').count(), 1)
        self.assert_axe_clean(page, 'search page with filters (phone, dark)')

    def test_empty_state_and_did_you_mean(self):
        page = self.open(device='mobile', dark=True)
        self.visit(page, '/search/?q=pyhton')
        self.assertEqual(page.locator('#search-results').count(), 0)
        link = page.locator('.search-did-you-mean a')
        self.assertEqual(link.inner_text(), 'python')
        self.submitted(page, link.click)
        self.assertEqual(self.results(page), ['Reading CSV files in Python'])

        self.visit(page, '/search/?q=zzzz+qqqq&status=accepted')
        self.assertIn('No questions match your search.', page.locator('.search-empty').inner_text())
        self.assertTrue(page.locator('.search-tips dl').is_visible())
        self.assertIn('is:unanswered', page.locator('.search-tips').inner_text())
        self.assert_axe_clean(page, 'empty search (phone, dark)')
        self.submitted(page, lambda: page.locator('.search-empty a', has_text='without the filters').click())
        self.assertEqual(params(page), {'q': 'zzzz qqqq'})

    def test_pagination_keeps_filters_and_keyboard(self):
        for i in range(16):
            question = self.create_question(self.alice, 'Python question number %d about csv' % i, tags='python csv')
            Question.objects.filter(id=question.id).update(score=i)
        page = self.open()
        self.visit(page, '/search/?q=csv&tags=python&min_score=0&pagesize=15')
        self.assertEqual(len(self.results(page)), 15)
        self.submitted(page, lambda: page.locator('.pager a', has_text='2').first.click())
        self.assertEqual(params(page)['tags'], 'python')
        self.assertEqual(params(page)['min_score'], '0')
        self.assertEqual(params(page)['page'], '2')
        self.assertEqual(len(self.results(page)), 2)
        self.assertEqual(page.locator('.search-chip').count(), 2)

        # j / k move through the results; Enter opens one.
        self.visit(page, '/search/?q=csv&tags=python&sort=votes')
        page.keyboard.press('j')
        page.keyboard.press('j')
        page.keyboard.press('k')
        current = page.locator('#search-results .is-kbd-current')
        self.assertEqual(current.locator('h2 a').inner_text(), 'Python question number 15 about csv')
        self.submitted(page, lambda: page.keyboard.press('Enter'))
        self.assertIn('/questions/', page.url)
