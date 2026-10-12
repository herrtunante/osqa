import socket
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs

from forum.models import Question, Answer, Comment

from .base import OSQATestCase


def akismet_reply(body):
    urlopen = mock.MagicMock()
    urlopen.return_value.__enter__.return_value.read.return_value = body
    return mock.patch('forum_modules.akismet.lib.akismet.urlopen', urlopen)


class AkismetTest(OSQATestCase):
    TITLE = 'Is this question spam or not?'

    def setUp(self):
        super().setUp()
        # The module is enabled in the test settings: its startup script wraps the views.
        from forum_modules.akismet import settings as akismet_settings
        self.api_key = akismet_settings.WORDPRESS_API_KEY
        self.set_key('test-key')
        self.addCleanup(setattr, self.api_key, '_temp', None)

        self.asker = self.create_user('alice')
        self.login(self.asker)

    def set_key(self, value):
        self.api_key.set_value(value)

    def ask(self, title=TITLE):
        return self.client.post('/questions/ask/', {
            'title': title,
            'text': 'Some body text that is long enough to pass validation',
            'tags': 'testing',
        }, HTTP_USER_AGENT='TestBrowser/1.0')

    def test_spam_question_is_not_posted(self):
        with akismet_reply(b'true') as urlopen:
            response = self.ask()

        self.assertContains(response, 'Akismet believes your question is spam')
        self.assertFalse(Question.objects.filter(title=self.TITLE).exists())

        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, 'https://rest.akismet.com/1.1/comment-check')
        self.assertTrue(urlopen.call_args[1]['timeout'])
        sent = parse_qs(request.data.decode('utf-8'))
        self.assertEqual(sent['api_key'], ['test-key'])
        self.assertEqual(sent['comment_type'], ['forum-post'])
        self.assertEqual(sent['comment_author'], ['alice'])
        self.assertEqual(sent['user_agent'], ['TestBrowser/1.0'])
        self.assertIn('long enough', sent['comment_content'][0])

    def test_ham_question_is_posted(self):
        with akismet_reply(b'false') as urlopen:
            self.assertEqual(self.ask().status_code, 302)
        self.assertTrue(urlopen.called)
        self.assertTrue(Question.objects.filter(title=self.TITLE).exists())

    def test_spam_answer_is_not_posted(self):
        question = Question(author=self.asker, title='A question title', body='Body text', tagnames='one')
        question.save()

        with akismet_reply(b'true') as urlopen:
            response = self.client.post('/questions/%d/answer/' % question.id,
                                        {'text': 'This is an answer with enough characters to pass.'})
        self.assertContains(response, 'Akismet believes your answer is spam')
        self.assertEqual(parse_qs(urlopen.call_args[0][0].data.decode())['comment_type'], ['reply'])
        self.assertFalse(Answer.objects.filter(parent=question).exists())

    def test_spam_comment_is_refused(self):
        question = Question(author=self.asker, title='A question title', body='Body text', tagnames='one')
        question.save()

        with akismet_reply(b'true'):
            result = self.command('/comment/%d/' % question.id, {'comment': 'Buy cheap things at my site now'})
        self.assertFalse(result['success'])
        self.assertIn('spam', result['error_message'])
        self.assertFalse(Comment.objects.filter(parent=question).exists())

    def test_unreachable_akismet_does_not_block_posting(self):
        for n, error in enumerate((URLError('no route to host'), socket.timeout('timed out'))):
            title = '%s %d' % (self.TITLE, n)
            with mock.patch('forum_modules.akismet.lib.akismet.urlopen', side_effect=error):
                self.assertEqual(self.ask(title).status_code, 302)
            self.assertTrue(Question.objects.filter(title=title).exists())

    def test_unexpected_answer_does_not_block_posting(self):
        with akismet_reply(b'invalid'):
            self.assertEqual(self.ask().status_code, 302)
        self.assertTrue(Question.objects.filter(title=self.TITLE).exists())

    def test_no_api_key_sends_nothing(self):
        self.set_key('')
        with akismet_reply(b'true') as urlopen:
            self.assertEqual(self.ask().status_code, 302)
        urlopen.assert_not_called()
        self.assertTrue(Question.objects.filter(title=self.TITLE).exists())

    def test_trusted_users_are_not_checked(self):
        self.login(self.create_user('bob', reputation=1000))
        with akismet_reply(b'true') as urlopen:
            self.assertEqual(self.ask().status_code, 302)
        urlopen.assert_not_called()
