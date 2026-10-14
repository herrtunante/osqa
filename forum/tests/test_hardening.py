import email
import json
import logging
import re
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from forum import settings
from forum.models import Question, Comment, User, ValidationHash
from forum.models.action import Action

from .base import OSQATestCase


class SettingMixin(object):
    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)


class LoginLockoutTest(OSQATestCase):
    def sign_in(self, password, username='carol', **extra):
        self.client.post('/account/local/signin/', {'username': username, 'password': password}, **extra)
        return self.client.session.get('_auth_user_id')

    def test_wrong_passwords_pause_the_account(self):
        carol = self.create_user('carol', password='right-pass')
        dave = self.create_user('dave', password='dave-pass')
        with self.assertLogs('forum.security', 'WARNING') as logs:
            for _ in range(5):
                self.assertIsNone(self.sign_in('wrong'))
            self.assertIsNone(self.sign_in('right-pass'))
        self.assertIn("event=login_failed ip=127.0.0.1 user=anonymous provider='local' username='carol'", logs.output[0])
        self.assertIn('event=login_paused', logs.output[-1])
        self.assertContains(self.client.get('/account/signin/'), 'Too many failed sign-in attempts')

        # Other accounts still work, and the pause ends.
        self.assertEqual(self.sign_in('dave-pass', 'dave'), str(dave.id))
        self.client.logout()
        from django.core.cache import cache
        cache.clear()
        self.assertEqual(self.sign_in('right-pass'), str(carol.id))

    def test_success_resets_the_count(self):
        carol = self.create_user('carol', password='right-pass')
        for _ in range(4):
            self.sign_in('wrong')
        self.assertEqual(self.sign_in('right-pass'), str(carol.id))
        self.client.logout()
        for _ in range(4):
            self.sign_in('wrong')
        self.assertEqual(self.sign_in('right-pass'), str(carol.id))

    def test_one_address_trying_many_accounts(self):
        for i in range(20):
            self.sign_in('wrong', 'nobody%d' % i)
        self.create_user('carol', password='right-pass')
        self.assertIsNone(self.sign_in('right-pass'))


class PasswordRulesTest(OSQATestCase):
    def register(self, password, username='carol'):
        return self.client.post('/account/local/register/', {
            'username': username, 'email': '%s@example.com' % username, 'password1': password,
            'password2': password, 'next': '/', 'form_token': self.signup_token()})

    def test_weak_passwords_are_refused(self):
        self.assertContains(self.register('Ab1'), 'too short')
        self.assertContains(self.register('password123'), 'too common')
        self.assertContains(self.register('12345678901'), 'entirely numeric')
        self.assertContains(self.register('carolcarol', username='carolcarol'), 'too similar')
        self.assertFalse(User.objects.exists())
        self.assertEqual(self.register('green-tree-river').status_code, 302)
        page = self.client.get('/account/local/register/')
        self.assertContains(page, 'At least 8 characters')

    def test_password_change_is_checked_logged_and_keeps_this_session(self):
        carol = self.create_user('carol', password='old-secret-1')
        self.login(carol)
        url = reverse('user_authsettings', kwargs={'id': carol.id})
        self.assertContains(self.client.post(url, {'oldpw': 'old-secret-1', 'password1': 'short',
                                                   'password2': 'short'}), 'too short')
        with self.assertLogs('forum.security', 'INFO') as logs:
            self.client.post(url, {'oldpw': 'old-secret-1', 'password1': 'new-secret-22', 'password2': 'new-secret-22'})
        self.assertIn('event=password_changed', logs.output[0])
        self.assertTrue(User.objects.get(id=carol.id).check_password('new-secret-22'))
        self.assertEqual(self.client.session.get('_auth_user_id'), str(carol.id))


class SignInLinkTest(SettingMixin, OSQATestCase):
    def request(self, email):
        return self.client.post('/account/tempsignin/', {'email': email}, follow=True)

    def test_same_answer_for_unknown_addresses(self):
        self.create_user('carol')
        known, unknown = self.request('carol@example.com'), self.request('nobody@example.com')
        self.assertContains(known, 'If this address belongs to an account')
        self.assertContains(unknown, 'If this address belongs to an account')
        self.assertNotContains(unknown, 'not on our database')
        self.assertEqual(len(self.sent_mail), 1)

    def test_links_are_random_keyed_and_single_use(self):
        carol = self.create_user('carol')
        self.request('carol@example.com')
        hash = ValidationHash.objects.get(user=carol, type='templogin')
        self.assertRegex(hash.hash_code, r'^[0-9a-f]{64}$')
        # A second request replaces the first link.
        self.request('carol@example.com')
        self.assertFalse(ValidationHash.objects.filter(hash_code=hash.hash_code).exists())
        link = re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[-1][3]).group(0)
        self.assertEqual(self.client.get(link).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_requests_are_limited(self):
        self.create_user('carol')
        for _ in range(3):
            self.request('carol@example.com')
        self.assertContains(self.request('carol@example.com'), 'Too many sign-in links')
        self.assertEqual(len(self.sent_mail), 3)


class PostingLimitTest(SettingMixin, OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', email_isvalid=True, reputation=100)
        self.login(self.alice)

    def ask(self, title):
        return self.client.post(reverse('ask'), {'title': title, 'text': 'Question body that is long enough.',
                                                 'tags': 'collect'})

    def test_questions_per_hour(self):
        self.set(settings.QUESTIONS_PER_HOUR, 1)
        self.assertEqual(self.ask('First question about exports').status_code, 302)
        page = self.ask('Second question about exports')
        self.assertContains(page, 'maximum number of questions')
        self.assertContains(page, 'Second question about exports')  # the text is kept
        self.assertEqual(Question.objects.count(), 1)

        self.alice.is_staff = True
        self.alice.save()
        self.assertEqual(self.ask('Second question about exports').status_code, 302)

    def test_comments_per_hour(self):
        self.set(settings.COMMENTS_PER_HOUR, 1)
        self.ask('A question to comment on')
        question = Question.objects.get()
        url = reverse('comment', kwargs={'id': question.id})
        self.command(url, {'comment': 'A first comment that is long enough.'})
        result = self.command(url, {'comment': 'A second comment that is long enough.'})
        self.assertFalse(result['success'])
        self.assertIn('maximum number of comments', result['error_message'])
        self.assertEqual(Comment.objects.count(), 1)


class ProxyAndHealthTest(OSQATestCase):
    def test_client_ip_from_the_trusted_proxy(self):
        alice = self.create_user('alice', email_isvalid=True)
        self.login(alice)
        forwarded = {'HTTP_X_FORWARDED_FOR': '6.6.6.6, 203.0.113.9'}
        data = {'title': 'Question from behind the proxy', 'text': 'Question body that is long enough.', 'tags': 'a'}
        self.client.post(reverse('ask'), data, **forwarded)
        self.assertEqual(Action.objects.get(action_type='ask').ip, '127.0.0.1')  # header ignored by default

        with override_settings(TRUSTED_PROXY_COUNT=1):
            data['title'] = 'Second question from behind the proxy'
            self.client.post(reverse('ask'), data, **forwarded)
        # The proxy appends what it saw; anything before it may be forged.
        self.assertEqual(Action.objects.filter(action_type='ask').latest('id').ip, '203.0.113.9')

    def test_health_check(self):
        response = self.client.get('/healthz', HTTP_HOST='osqa-container:8000')
        self.assertEqual((response.status_code, response.content), (200, b'ok\n'))
        with mock.patch('forum.middleware.security.connection') as connection:
            connection.cursor.side_effect = Exception('down')
            self.assertEqual(self.client.get('/healthz').status_code, 503)


class SecurityHeadersTest(SettingMixin, OSQATestCase):
    def test_headers(self):
        response = self.client.get('/')
        csp = response['Content-Security-Policy']
        for part in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'self'", "base-uri 'self'",
                     "report-uri /csp-report/", "https://www.gstatic.com"):
            self.assertIn(part, csp)
        self.assertNotIn('upgrade-insecure-requests', csp)
        self.assertIn('camera=()', response['Permissions-Policy'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertNotIn('Content-Security-Policy', self.client.get('/healthz'))

        self.set(settings.CSP_EXTRA_SOURCES, 'https://plausible.io\nhttps://cdn.example.org')
        csp = self.client.get('/')['Content-Security-Policy']
        self.assertIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn('https://plausible.io https://cdn.example.org;', csp)

        with override_settings(CONTENT_SECURITY_POLICY_REPORT_ONLY=True):
            self.assertIn('Content-Security-Policy-Report-Only', self.client.get('/'))
        with override_settings(CONTENT_SECURITY_POLICY=None):
            self.assertNotIn('Content-Security-Policy', self.client.get('/'))

    def test_violation_reports_are_logged(self):
        report = {'csp-report': {'violated-directive': 'script-src', 'blocked-uri': 'https://evil.example/x.js',
                                 'document-uri': 'http://testserver/'}}
        with self.assertLogs('forum.security', 'WARNING') as logs:
            response = self.client.post('/csp-report/', json.dumps(report), content_type='application/csp-report')
        self.assertEqual(response.status_code, 204)
        self.assertIn("blocked='https://evil.example/x.js'", logs.output[0])
        self.assertEqual(self.client.get('/csp-report/').status_code, 405)


class SecurityLogTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner', is_superuser=True, is_staff=True)
        self.alice = self.create_user('alice')

    def test_admin_changes_are_logged(self):
        self.login(self.owner)
        with self.assertLogs('forum.security', 'INFO') as logs:
            self.command('/users/%d/powers/grant/staff/' % self.alice.id)
            self.client.post(reverse('admin_set', args=['security']), {
                'LOGIN_FAILURES_LIMIT': 7, 'LOGIN_LOCKOUT_MINUTES': 15, 'PASSWORD_RESETS_PER_HOUR': 3,
                'QUESTIONS_PER_HOUR': 10, 'ANSWERS_PER_HOUR': 20, 'COMMENTS_PER_HOUR': 40, 'CSP_EXTRA_SOURCES': ''})
        self.assertIn("event=permissions_changed ip=127.0.0.1 user=owner(id=%d) account=alice(id=%d) change='grant staff'"
                      % (self.owner.id, self.alice.id), logs.output[0])
        self.assertIn("event=settings_changed", logs.output[1])
        self.assertIn("fields='LOGIN_FAILURES_LIMIT'", logs.output[1])
        self.assertEqual(int(settings.LOGIN_FAILURES_LIMIT), 7)
        settings.LOGIN_FAILURES_LIMIT._temp = None

    def test_refused_pages_are_logged(self):
        bob = self.create_user('bob')
        self.login(bob)
        with self.assertLogs('forum.security', 'INFO') as logs:
            response = self.client.get(reverse('user_authsettings', kwargs={'id': self.alice.id}))
        self.assertEqual(response.status_code, 403)
        self.assertIn('event=permission_denied', logs.output[0])


class AdminErrorEmailTest(OSQATestCase):
    def test_errors_are_emailed_once(self):
        from forum.utils.log import AdminEmailHandler
        self.create_user('owner', is_superuser=True)
        settings.EMAIL_HOST.set_value('smtp.example.org')
        self.addCleanup(setattr, settings.EMAIL_HOST, '_temp', None)
        logger = logging.getLogger('test.admin_email')
        logger.propagate = False
        handler = AdminEmailHandler()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        with mock.patch('forum.utils.mail.create_connection') as create_connection:
            for _ in range(3):
                try:
                    1 / 0
                except ZeroDivisionError:
                    logger.exception('Internal Server Error: /questions/')
        sendmail = create_connection.return_value.sendmail
        self.assertEqual(sendmail.call_count, 1)
        sender, recipients, message = sendmail.call_args[0]
        self.assertEqual(recipients, ['owner@example.com'])
        message = email.message_from_string(message)
        self.assertEqual(message['Subject'], '[OSQA] ERROR: Internal Server Error: /questions/')
        self.assertIn('ZeroDivisionError', message.get_payload(decode=True).decode())
