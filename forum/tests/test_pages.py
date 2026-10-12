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
