from forum.actions import VoteUpAction
from forum.models import Question

from .base import OSQATestCase


class AnonymousPagesTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user('alice')
        self.question = Question(author=self.user, title='A question title', body='Body text', tagnames='one')
        self.question.save()

    def test_pages_render(self):
        for url in ['/', '/questions/', '/questions/unanswered/', '/tags/', '/users/', '/badges/',
                    '/account/signin/', '/cstyle.css', self.question.get_absolute_url(),
                    self.user.get_profile_url()]:
            with self.subTest(url=url):
                self.assertCleanPage(self.client.get(url))

    def test_question_list_links_to_question(self):
        response = self.client.get('/questions/')
        self.assertContains(response, 'href="%s"' % self.question.get_absolute_url())

    def test_feed_and_sitemap(self):
        self.assertContains(self.client.get('/feeds/rss/'), 'A question title')
        self.assertContains(self.client.get('/sitemap.xml'), 'http://testserver/sitemap-questions.xml')


class PaginationTest(OSQATestCase):
    def test_later_pages(self):
        user = self.create_user('alice')
        # 8 pages of 15: the page links around page 4 are computed from
        # visible_page_range / 2, which must stay an integer.
        for i in range(106):
            Question(author=user, title='Question number %d title' % i, body='Body text', tagnames='one').save()

        for page in (1, 4, 8):
            with self.subTest(page=page):
                self.assertCleanPage(self.client.get('/questions/?page=%d&pagesize=15' % page))

    def test_reputation_tab_with_history(self):
        alice = self.create_user('alice')
        bob = self.create_user('bob', reputation=1000)
        question = Question(author=alice, title='A question title', body='Body text', tagnames='one')
        question.save()
        VoteUpAction(user=bob, node=question, ip='127.0.0.1').save()

        self.assertTrue(alice.reputes.exists())
        self.assertCleanPage(self.client.get(alice.get_profile_url() + '/reputation/'))


class RobotsTxtTest(OSQATestCase):
    def test_robots_txt(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')


class LayoutTest(OSQATestCase):
    def test_mobile_viewport_and_stylesheet(self):
        response = self.client.get('/')
        self.assertContains(response, '<!DOCTYPE html>')
        self.assertContains(response, '<meta name="viewport" content="width=device-width, initial-scale=1" />')
        self.assertContains(response, '/media/style/modern.css')
        self.assertNotContains(response, 'http://www.google.com/jsapi')

    def test_logo_url_has_no_double_slash(self):
        from forum import settings
        self.addCleanup(setattr, settings.APP_LOGO, '_temp', None)
        for value, src in [('/upfiles/logo.png', 'src="/upfiles/logo.png"'),
                           ('upfiles/logo.png', 'src="/upfiles/logo.png"'),
                           ('https://cdn.example.org/logo.png', 'src="https://cdn.example.org/logo.png"')]:
            with self.subTest(value=value):
                settings.APP_LOGO.set_value(value)
                self.assertContains(self.client.get('/'), src)

    def test_search_box(self):
        from forum import settings
        self.addCleanup(setattr, settings.GOOGLE_CSE_ID, '_temp', None)

        settings.GOOGLE_CSE_ID.set_value('')
        response = self.client.get('/')
        self.assertContains(response, 'name="q" id="keywords"')
        self.assertNotContains(response, 'cse.google.com')

        settings.GOOGLE_CSE_ID.set_value('123:abc')
        response = self.client.get('/')
        self.assertContains(response, "encodeURIComponent('123:abc')")
        self.assertNotContains(response, 'id="keywords"')
