import re

from django.test import Client

from forum.models import Question, QuestionSubscription, User
from forum.actions import DeleteAction, FlagAction
from forum.utils.redact import redact_params, redact_meta, REDACTED

from .base import OSQATestCase

XSS = '<img src=x onerror=alert(1)>'


class QuestionMixin:
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner', is_superuser=True, is_staff=True)
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob', reputation=1000)
        self.question = Question(author=self.alice, title='A question title', body='Body text', tagnames='one')
        self.question.save()


class CommandMethodTest(QuestionMixin, OSQATestCase):
    def test_commands_reject_get(self):
        self.login(self.bob)
        for url in ['/vote/%d/up/' % self.question.id, '/mark_favorite/%d/' % self.question.id,
                    '/delete/%d/' % self.question.id, '/subscribe/%d/' % self.question.id]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, **self.AJAX).status_code, 405)

        question = Question.objects.get(id=self.question.id)
        self.assertEqual(question.score, 0)
        self.assertFalse(question.nis.deleted)

    def test_prompt_commands_show_form_on_get_without_acting(self):
        self.login(self.bob)
        response = self.client.get('/flag/%d/' % self.question.id, **self.AJAX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FlagAction.objects.filter(node=self.question).exists())

    def test_user_powers_requires_post(self):
        self.login(self.owner)
        url = '/users/%d/powers/grant/super/' % self.alice.id
        self.assertEqual(self.client.get(url, **self.AJAX).status_code, 405)
        self.assertFalse(User.objects.get(id=self.alice.id).is_superuser)

        self.assertTrue(self.command(url)['success'])
        self.assertTrue(User.objects.get(id=self.alice.id).is_superuser)

    def test_only_site_owner_removes_superusers(self):
        self.assertTrue(self.owner.is_siteowner)
        other_admin = self.create_user('other', is_superuser=True, is_staff=True)
        self.login(other_admin)
        result = self.command('/users/%d/powers/remove/super/' % self.owner.id)
        self.assertFalse(result['success'])
        self.assertTrue(User.objects.get(id=self.owner.id).is_superuser)


class CsrfTest(QuestionMixin, OSQATestCase):
    def test_ajax_post_with_page_token(self):
        """The JS sends the token rendered by {% csrf_token %} (there is no csrftoken cookie)."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.bob)
        url = '/vote/%d/up/' % self.question.id

        self.assertEqual(client.post(url, **self.AJAX).status_code, 403)

        page = client.get(self.question.get_absolute_url()).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        response = client.post(url, HTTP_X_CSRFTOKEN=token, **self.AJAX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Question.objects.get(id=self.question.id).score, 1)


class EscapingTest(QuestionMixin, OSQATestCase):
    def test_question_title_escaped_in_activity(self):
        self.login(self.alice)
        self.client.post('/questions/ask/', {'title': 'Evil ' + XSS + ' title here', 'tags': 'one',
                                             'text': 'Some body text that is long enough to pass validation'})
        page = self.client.get(self.alice.get_profile_url() + '/recent/').content.decode()
        self.assertIn('Evil', page)
        self.assertNotIn(XSS, page)

    def test_flag_reason_escaped(self):
        FlagAction(user=self.bob, node=self.question, extra=XSS, ip='127.0.0.1').save()
        self.login(self.owner)
        page = self.client.get('/admin/flagged_posts/').content.decode()
        self.assertIn('&lt;img src=x', page)
        self.assertNotIn(XSS, page)


class PermissionTest(QuestionMixin, OSQATestCase):
    def test_anonymous_cannot_change_others_subscription(self):
        url = '/subscribe/%d/%d' % (self.question.id, self.bob.id)
        self.assertFalse(self.command(url)['success'])
        self.assertFalse(QuestionSubscription.objects.filter(question=self.question, user=self.bob).exists())

    def test_user_cannot_change_others_subscription(self):
        self.login(self.alice)
        self.assertFalse(self.command('/subscribe/%d/%d' % (self.question.id, self.bob.id))['success'])

    def test_deleted_post_is_hidden(self):
        DeleteAction(user=self.owner, node=self.question, ip='127.0.0.1').save()

        self.assertEqual(self.client.get('/revisions/%d/' % self.question.id).status_code, 404)
        self.login(self.bob)
        self.assertEqual(self.client.get('/revisions/%d/' % self.question.id).status_code, 404)
        response = self.client.get('/node_markdown/%d/' % self.question.id, **self.AJAX)
        self.assertNotIn(b'Body text', response.content)

        self.login(self.owner)
        self.assertEqual(self.client.get('/revisions/%d/' % self.question.id).status_code, 200)

    def test_admin_actions_require_post(self):
        self.login(self.owner)
        for url in ['/admin/go_defaults/', '/admin/go_bootstrap/', '/admin/denormalize/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)


class RedirectAndLoggingTest(OSQATestCase):
    def test_offsite_referer_not_used_after_login(self):
        self.client.get('/account/signin/', HTTP_REFERER='http://evil.example/phish')
        self.assertNotIn('on_signin_url', str(dict(self.client.session)))
        self.create_user('carol', password='right-pass')
        response = self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.assertNotIn('evil.example', response['Location'])

    def test_redaction(self):
        self.assertEqual(dict(redact_params({'username': 'u', 'password': 'p', 'csrfmiddlewaretoken': 't'})),
                         {'username': 'u', 'password': REDACTED, 'csrfmiddlewaretoken': REDACTED})
        self.assertEqual(dict(redact_meta({'HTTP_COOKIE': 'sessionid=x', 'PATH_INFO': '/'})),
                         {'HTTP_COOKIE': REDACTED, 'PATH_INFO': '/'})

    def test_security_headers(self):
        response = self.client.get('/')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
