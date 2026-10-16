"""What anonymous visitors (and search engines, agents) can see."""
import re

from .base import E2ETestCase


class PublicPagesTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=200)
        self.bob = self.create_user('bob', reputation=50)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.answer = self.create_answer(self.bob, self.question,
                                         'Use the **CSV export** in the Collect designer:\n\n```\nFile > Export\n```')
        self.create_comment(self.alice, self.answer, 'Thanks, @bob, that worked for me.')
        self.unanswered = self.create_question(self.bob, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')

    PAGES = [
        '/', '/questions/', '/questions/?sort=newest', '/questions/?sort=mostvoted', '/questions/?sort=hottest',
        '/questions/unanswered/', '/tags/', '/tags/collect/', '/users/', '/badges/', '/faq/', '/about/', '/privacy/',
        '/search/?q=export', '/search/?q=collect&t=tag', '/search/?q=bob&t=user', '/account/signin/',
        '/account/local/register/', '/help/email/', '/questions/ask/',
    ]

    def test_pages_on_desktop_phone_and_dark_mode(self):
        pages = self.PAGES + [self.question.get_absolute_url(), self.unanswered.get_absolute_url(),
                              self.alice.get_profile_url()]
        for device, dark in (('desktop', False), ('mobile', True)):
            page = self.open(device=device, dark=dark)
            for path in pages:
                with self.subTest(device=device, path=path):
                    self.visit(page, path)

    def test_phone_header_tabs_and_avatar_fallback(self):
        page = self.open(device='mobile', dark=True)
        page.route('**gravatar.com/**', lambda route: route.fulfill(status=404, body=''))
        for path in ('/', '/badges/', '/users/'):
            with self.subTest(path=path):
                self.visit(page, path)
                tabs, ask = page.locator('#nav .nav-tabs'), page.locator('#nav_ask')
                row, button = tabs.bounding_box(), ask.bounding_box()
                # "ask a question" sits beside the tabs, never over them.
                self.assertLessEqual(row['x'] + row['width'], button['x'] + 1, (row, button))
                self.assertLessEqual(button['x'] + button['width'], 390)
                # The current tab is scrolled into view; every tab can be.
                current = page.locator('#nav a.on').bounding_box()
                self.assertGreaterEqual(current['x'], row['x'] - 1)
                self.assertLessEqual(current['x'] + current['width'], row['x'] + row['width'] + 1)
                for link in page.locator('#nav .nav-tabs a').all():
                    link.scroll_into_view_if_needed()
                    box = link.bounding_box()
                    self.assertGreaterEqual(box['x'], row['x'] - 1, link.inner_text())
                    self.assertLessEqual(box['x'] + box['width'], button['x'] + 1, link.inner_text())
                # A row that scrolls says so with a fade on the hidden side.
                if tabs.evaluate('el => el.scrollWidth > el.clientWidth + 1'):
                    self.assertTrue(tabs.evaluate("el => el.matches('.fade-start, .fade-end')"))
        # Avatars that can't be loaded show the initial, not a broken image.
        self.visit(page, self.question.get_absolute_url())
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        page.wait_for_function("() => [...document.querySelectorAll('img.gravatar')].every("
                               "img => img.classList.contains('gravatar-fallback') && img.complete && img.naturalWidth > 0)")
        self.assertGreater(page.locator('img.gravatar.gravatar-fallback').count(), 0)
        self.check_page(page)

    def test_question_page_content(self):
        page = self.open()
        self.visit(page, self.question.get_absolute_url())
        self.assertIn('How do I export Collect survey data to CSV?', page.locator('h1').first.inner_text())
        self.assertEqual(page.locator('.answer').count(), 1)
        self.assertIn('File > Export', page.locator('.answer pre').inner_text())
        self.assertEqual(page.locator('.comment').count(), 1)
        # Anonymous visitors get the sign-in prompt instead of voting.
        page.locator('#post-%d-upvote' % self.answer.id).click()
        self.wait_for_ajax(page)
        self.assertTrue(page.locator('.dialog, .notify, .error').first.is_visible() or
                        page.url.endswith('/account/signin/'))
        self.check_page(page)

    def test_search_and_tags(self):
        page = self.open()
        self.visit(page, '/')
        page.fill('#keywords', 'export')
        page.locator('#searchBar button[type=submit]').click()
        page.wait_for_load_state()
        self.assertIn('/search/', page.url)
        self.assertIn('How do I export Collect survey data to CSV?', page.locator('#search-results h2 a').all_inner_texts())
        self.check_page(page)

        self.visit(page, '/tags/')
        page.get_by_role('link', name='sepal', exact=True).first.click()
        page.wait_for_load_state()
        self.assertIn('Can SEPAL process Sentinel-2 time series?', page.content())
        self.check_page(page)

    def test_machine_readable_endpoints(self):
        page = self.open()
        for path, expected in (('/questions/%d.md' % self.question.id, 'How do I export Collect survey data'),
                               ('/llms.txt', 'How do I export'), ('/export/qa.jsonl', 'export'),
                               ('/feeds/rss/', 'export'), ('/sitemap.xml', 'sitemap'), ('/healthz', 'ok')):
            with self.subTest(path=path):
                response = page.request.get(self.url(path))
                self.assertEqual(response.status, 200, path)
                self.assertIn(expected, response.text())

    def test_sitemap_leads_to_every_question(self):
        # As a search engine reads it: the index, each section, its pages.
        page = self.open()
        self.visit(page, '/')
        fetch = '''async path => { const r = await fetch(path); return [r.status, await r.text()]; }'''

        def locations(path):
            status, text = page.evaluate(fetch, path)
            self.assertEqual(status, 200, path)
            return [loc.split('://', 1)[-1].split('/', 1)[-1] for loc in re.findall(r'<loc>([^<]+)</loc>', text)]

        questions = []
        for section in locations('/sitemap.xml'):
            for sitemap_page in locations('/' + section):
                questions += locations('/' + sitemap_page)
        for question in (self.question, self.unanswered):
            self.assertIn(question.get_absolute_url().lstrip('/'), questions)
        self.check_page(page)

    def test_not_found_page(self):
        page = self.open()
        response = page.goto(self.url('/questions/999999/'))
        self.assertEqual(response.status, 404)
        page.problems.clear()  # the 404 response itself is logged by the browser

    def test_language_switcher(self):
        page = self.open(locale='es-ES')
        self.visit(page, '/')
        self.assertEqual(page.get_attribute('html', 'lang'), 'es')
        with page.expect_navigation():
            page.select_option('#language-select', 'fr')
        self.assertEqual(page.get_attribute('html', 'lang'), 'fr')
        self.visit(page, '/questions/?sort=mostvoted')
        self.assertEqual(page.get_attribute('html', 'lang'), 'fr')
