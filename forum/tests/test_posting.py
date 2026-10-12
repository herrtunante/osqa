from forum.models import Question, Answer, Comment

from .base import OSQATestCase


class PostingTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.asker = self.create_user('alice')
        self.answerer = self.create_user('bob', reputation=1000)

    def ask(self):
        self.login(self.asker)
        response = self.client.post('/questions/ask/', {
            'title': 'How do I test this thing properly?',
            'text': 'Some **body** text that is long enough to pass validation',
            'tags': 'testing django',
        })
        self.assertEqual(response.status_code, 302)
        return Question.objects.get(title='How do I test this thing properly?')

    def answer(self, question):
        self.login(self.answerer)
        response = self.client.post('/questions/%d/answer/' % question.id,
                                    {'text': 'This is an answer with enough characters to pass.'})
        self.assertEqual(response.status_code, 302)
        return Answer.objects.get(parent=question)

    def test_ask_renders_body(self):
        question = self.ask()
        self.assertIn('<strong>body</strong>', question.body)
        self.assertEqual(sorted(question.tagname_list()), ['django', 'testing'])
        self.assertCleanPage(self.client.get(question.get_absolute_url()))

    def test_answer_shows_on_question_page(self):
        question = self.ask()
        answer = self.answer(question)
        self.assertEqual(Question.objects.get(id=question.id).answer_count, 1)
        self.assertContains(self.client.get(question.get_absolute_url()), 'enough characters to pass')
        self.assertEqual(answer.question, question)

    def test_vote_comment_favorite_accept(self):
        question = self.ask()
        answer = self.answer(question)

        self.login(self.answerer)
        self.assertTrue(self.command('/vote/%d/up/' % question.id)['success'])
        self.assertTrue(self.command('/comment/%d/' % question.id, {'comment': 'A comment that is long enough.'})['success'])
        self.assertTrue(self.command('/mark_favorite/%d/' % question.id)['success'])

        self.login(self.asker)
        own_vote = self.command('/vote/%d/up/' % question.id)
        self.assertFalse(own_vote['success'])
        self.assertIn('cannot vote your own post', own_vote['error_message'])
        self.assertTrue(self.command('/accept_answer/%d/' % answer.id)['success'])

        question = Question.objects.get(id=question.id)
        self.assertEqual(question.score, 1)
        self.assertEqual(question.favorite_count, 1)
        self.assertEqual(question.accepted_count, 1)
        self.assertEqual(Comment.objects.filter(parent=question).count(), 1)
        self.assertContains(self.client.get(question.get_absolute_url()), 'A comment that is long enough.')

    def test_user_pages(self):
        question = self.ask()
        self.answer(question)
        self.login(self.asker)
        profile = self.asker.get_profile_url()
        for url in [profile, profile + '/recent/', profile + '/favorites/', profile + '/votes/',
                    '/revisions/%d/' % question.id]:
            with self.subTest(url=url):
                self.assertCleanPage(self.client.get(url))


class AdminPagesTest(OSQATestCase):
    def test_admin_pages(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.login(admin)
        for url in ['/admin/', '/admin/settings/basic/', '/admin/flagged_posts/']:
            with self.subTest(url=url):
                self.assertCleanPage(self.client.get(url))


class PendingSubmissionTest(OSQATestCase):
    """Anonymous submissions are kept in the (JSON-serialized) session until login."""

    def test_question_asked_before_login_is_posted_after_login(self):
        self.create_user('carol', password='right-pass')
        self.client.post('/questions/ask/', {
            'title': 'A question asked before logging in',
            'text': 'Some body text that is long enough to pass validation',
            'tags': 'pending',
        })
        self.assertFalse(Question.objects.filter(title='A question asked before logging in').exists())

        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'}, follow=True)
        question = Question.objects.get(title='A question asked before logging in')
        self.assertEqual(question.author.username, 'carol')

    def test_question_page_records_view_once(self):
        user = self.create_user('alice')
        question = Question(author=user, title='A question title', body='Body text', tagnames='one')
        question.save()
        self.client.get(question.get_absolute_url())
        self.client.get(question.get_absolute_url())
        self.assertEqual(Question.objects.get(id=question.id).extra_count, 1)
