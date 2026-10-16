"""The account lifecycle (forum/utils/accounts.py, forum/views/auth.py,
forum/views/account.py): sign-up with double opt-in, confirmation links,
email changes, password rules, resets and changes, sessions (sign out,
everywhere, remember me, new-browser emails) and deleting an account."""
import datetime
import io
import re
from unittest import mock

from django.contrib.auth import SESSION_KEY
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from forum import settings
from forum.models import (Answer, AuthKeyUserAssociation, MarkedTag, Notification, Passkey, PendingEmail,
                          PushSubscription, Question, QuestionSubscription, RecoveryCode, Tag, TOTPDevice, User,
                          ValidationHash)
from forum.models.user import UserProperty
from forum.utils import accounts, passwords, two_factor

from .base import OSQATestCase

PASSWORD = 'green-tree-river-7'
LINK = r'http://testserver(/account/%s/\d+/[^/\s]+/)'


class AccountTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner', is_superuser=True, is_staff=True, email_isvalid=True)

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def register(self, username='carol', email=None, password=PASSWORD, client=None):
        return (client or self.client).post('/account/local/register/', {
            'username': username, 'email': email or '%s@example.com' % username,
            'password1': password, 'password2': password, 'next': '/', 'form_token': self.signup_token()})

    def sign_in(self, username='carol', password=PASSWORD, client=None, remember='1'):
        data = {'username': username, 'password': password}
        if remember:
            data['remember'] = remember
        return (client or self.client).post('/account/local/signin/', data)

    def signed_in_id(self, client=None):
        return (client or self.client).session.get(SESSION_KEY)

    def mails(self, words, email=None):
        return [m for m in self.sent_mail if words in m[1] and (email is None or m[0].email == email)]

    def link(self, mail, kind):
        return re.search(LINK % kind, mail[3]).group(1)

    def ask(self, client=None, title='How do I export my survey data?'):
        return (client or self.client).post('/questions/ask/', {
            'title': title, 'tags': 'collect', 'text': 'Some body text that is long enough to pass validation.'})

    def other_session(self, user):
        client = Client()
        client.force_login(user)
        # Like a real sign-in: the session remembers the account's epoch.
        session = client.session
        session[accounts.EPOCH_SESSION_KEY] = User.objects.get(id=user.id).session_epoch
        session[accounts.AUTH_TIME_SESSION_KEY] = datetime.datetime.now().isoformat()
        session.save()
        return client

    def still_signed_in(self, client):
        return client.get('/').wsgi_request.user.is_authenticated


class SignUpConfirmationTest(AccountTestCase):
    """The 'post' mode: new members sign in at once but confirm before posting."""

    def setUp(self):
        super().setUp()
        self.set(settings.EMAIL_CONFIRMATION_MODE, 'post')

    def test_new_members_sign_in_but_confirm_before_posting(self):
        self.register()
        carol = User.objects.get(username='carol')
        self.assertTrue(carol.email_confirmation_required)
        self.assertFalse(carol.email_isvalid)
        self.assertEqual(self.signed_in_id(), str(carol.id))

        welcome = self.mails('Welcome')
        self.assertEqual(len(welcome), 1)
        self.assertEqual(welcome[0][1], 'Welcome to OSQA: please confirm your email')
        self.assertEqual(welcome[0][5], {})  # an account email: no unsubscribe
        self.assertIn('The link works once, for 48 hours.', welcome[0][3])

        page = self.client.get('/')
        self.assertContains(page, 'Confirm your email address to start posting.')
        self.assertContains(page, 'carol@example.com')

        # Asking is held until the address is confirmed.
        self.ask()
        self.assertFalse(Question.objects.filter(author=carol).exists())
        question = Question(author=self.owner, title='A question for comments', body='Body text', tagnames='x')
        question.save()
        result = self.command('/comment/%d/' % question.id, {'comment': 'A comment that is long enough.'})
        self.assertFalse(result['success'])
        self.assertIn('Confirm your email address to comment', result['error_message'])

        # The link confirms; the question written before is published.
        response = self.client.get(self.link(welcome[0], 'validate'), follow=True)
        self.assertTrue(Question.objects.filter(author=carol).exists())
        self.assertEqual(response.status_code, 200)
        carol = User.objects.get(id=carol.id)
        self.assertTrue(carol.email_isvalid)
        self.assertFalse(carol.email_confirmation_required)
        self.assertNotContains(self.client.get('/'), 'Confirm your email address to start posting.')
        own = Question.objects.get(author=carol)
        self.assertTrue(self.command('/comment/%d/' % own.id, {'comment': 'A comment that is long enough.'})['success'])

    def test_the_link_is_single_use_digest_stored_and_expires(self):
        self.register()
        carol = User.objects.get(username='carol')
        link = self.link(self.mails('Welcome')[0], 'validate')
        token = link.rstrip('/').rsplit('/', 1)[1]
        row = ValidationHash.objects.get(user=carol, type='email')
        self.assertNotEqual(row.hash_code, token)
        self.assertNotIn(token, row.hash_code)
        self.assertGreater(row.expiration, datetime.datetime.now() + datetime.timedelta(hours=47))

        ValidationHash.objects.filter(id=row.id).update(expiration=datetime.datetime.now() - datetime.timedelta(1))
        page = self.client.get(link)
        self.assertEqual(page.status_code, 410)
        self.assertContains(page, 'This link doesn', status_code=410)
        self.assertFalse(User.objects.get(id=carol.id).email_isvalid)

        # A new link (the old one is gone); used twice, the second visit says so.
        self.client.post(reverse('send_validation_email'))
        link = self.link(self.mails('Confirm your email address')[-1], 'validate')
        self.client.logout()
        page = self.client.get(link)
        self.assertContains(page, 'your email is now validated')
        self.assertIsNone(self.signed_in_id())  # the link never signs in
        self.assertContains(self.client.get(link), 'already confirmed')

    def test_wrong_codes_and_other_peoples_links_fail(self):
        self.register()
        carol = User.objects.get(username='carol')
        dave = self.create_user('dave')
        link = self.link(self.mails('Welcome')[0], 'validate')
        self.assertEqual(self.client.get(link.replace('/%d/' % carol.id, '/%d/' % dave.id)).status_code, 410)
        self.assertEqual(self.client.get('/account/validate/%d/%s/' % (carol.id, 'f' * 64)).status_code, 410)
        self.assertFalse(User.objects.get(id=carol.id).email_isvalid)

    def test_site_owner_and_existing_members_are_not_affected(self):
        User.objects.all().delete()
        self.register('founder')
        founder = User.objects.get(username='founder')
        self.assertTrue(founder.is_superuser)
        self.assertFalse(founder.email_confirmation_required)
        # Members from before (and accounts made by admins or imports) keep posting.
        old = self.create_user('oldtimer')
        self.assertFalse(old.email_confirmation_required)
        self.login(old)
        self.ask()
        self.assertTrue(Question.objects.filter(author=old).exists())
        self.assertNotContains(self.client.get('/'), 'Confirm your email address to start posting.')

    def test_mode_off(self):
        self.set(settings.EMAIL_CONFIRMATION_MODE, 'off')
        self.register()
        self.ask()
        self.assertTrue(Question.objects.filter(author__username='carol').exists())

    def test_legacy_list_still_applies_to_everyone(self):
        self.set(settings.REQUIRE_EMAIL_VALIDATION_TO, ['ask'])
        old = self.create_user('oldtimer')
        self.login(old)
        self.ask()
        self.assertFalse(Question.objects.filter(author=old).exists())

    def test_resend_is_limited_and_replaces_the_link(self):
        self.register()
        first = self.link(self.mails('Welcome')[0], 'validate')
        self.assertContains(self.client.get(reverse('send_validation_email')), 'Send a new link')
        for _i in range(3):
            self.client.post(reverse('send_validation_email'))
        self.assertEqual(len(self.mails('Confirm your email address')), 3)
        response = self.client.post(reverse('send_validation_email'), follow=True)
        self.assertContains(response, 'Too many confirmation emails')
        self.assertEqual(len(self.mails('Confirm your email address')), 3)
        self.assertEqual(self.client.get(first).status_code, 410)

    def test_signed_out_resend_does_not_tell_which_addresses_exist(self):
        self.register()
        self.client.logout()
        self.sent_mail.clear()
        known = self.client.post(reverse('send_validation_email'), {'email': 'carol@example.com'})
        unknown = self.client.post(reverse('send_validation_email'), {'email': 'nobody@example.com'})
        confirmed = self.client.post(reverse('send_validation_email'), {'email': 'owner@example.com'})
        card = lambda r: re.search(r'<div class="auth-card".*?</p>', r.content.decode(), re.S).group(0)
        self.assertEqual(card(known), card(unknown))
        self.assertEqual(card(known), card(confirmed))
        self.assertEqual([m[0].email for m in self.sent_mail], ['carol@example.com'])

    def test_unconfirmed_accounts_are_cleaned_up(self):
        self.register()
        self.register('dave', client=Client())
        self.register('erin', client=Client())
        long_ago = datetime.datetime.now() - datetime.timedelta(days=8)
        User.objects.filter(username__in=('carol', 'dave', 'erin')).update(date_joined=long_ago)
        dave = User.objects.get(username='dave')
        Question(author=dave, title='A real question title', tagnames='x', body='A body that is long enough.').save()
        User.objects.filter(username='erin').update(email_isvalid=True)
        out = io.StringIO()
        call_command('cleanup_inactive_users', stdout=out)
        self.assertIn('Deleted 1 unconfirmed new account(s) older than 7 days', out.getvalue())
        self.assertEqual(set(User.objects.values_list('username', flat=True)), {'owner', 'dave', 'erin'})

        self.set(settings.EMAIL_CONFIRMATION_MODE, 'off')
        User.objects.filter(username='erin').update(email_isvalid=False, email_confirmation_required=True)
        call_command('cleanup_inactive_users', stdout=io.StringIO())
        self.assertTrue(User.objects.filter(username='erin').exists())


class ConfirmBeforeSignInTest(AccountTestCase):
    """The default mode: new members confirm before they can sign in."""

    def test_sign_up_then_confirm_then_sign_in(self):
        self.assertEqual(settings.EMAIL_CONFIRMATION_MODE.default, 'signin')
        self.assertEqual(accounts.confirmation_mode(), 'signin')
        page = self.client.get('/account/local/register/')
        self.assertContains(page, 'finish creating your account')
        response = self.register()
        self.assertRedirects(response, reverse('auth_check_email'), fetch_redirect_response=False)
        self.assertIsNone(self.signed_in_id())
        welcome = self.mails('Welcome')[0]
        self.assertIn('Then you can sign in.', welcome[3])

        # The right password doesn't sign in yet; a new link is sent (and limited).
        response = self.sign_in()
        self.assertRedirects(response, reverse('auth_check_email'), fetch_redirect_response=False)
        self.assertIsNone(self.signed_in_id())
        self.assertEqual(len(self.mails('Confirm your email address')), 1)
        # A wrong password says nothing about confirming.
        count = len(self.sent_mail)
        self.sign_in(password='wrong-password')
        self.assertEqual(len(self.sent_mail), count)
        self.assertContains(self.client.get('/account/signin/'), 'or email and password')

        # The new link replaced the one in the welcome email.
        self.assertEqual(self.client.get(self.link(welcome, 'validate')).status_code, 410)
        self.client.get(self.link(self.mails('Confirm your email address', 'carol@example.com')[0], 'validate'))
        self.sign_in()
        self.assertEqual(self.signed_in_id(), str(User.objects.get(username='carol').id))

    def test_taken_address_gets_the_same_answer(self):
        self.create_user('alice', email_isvalid=True)
        new = self.register('newbie')
        taken = self.register('another', email='alice@example.com', client=Client())
        self.assertEqual(new['Location'], taken['Location'])
        self.assertFalse(User.objects.filter(username='another').exists())
        exists = self.mails('You already have an account')
        self.assertEqual([m[0].username for m in exists], ['alice'])
        self.assertIn('No new account was created', exists[0][3])


class PasswordRulesTest(AccountTestCase):
    def test_nist_rules(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        validate_password('1234567890123456')  # no composition rules
        validate_password('correct horse battery staple')  # spaces are fine
        validate_password('x' * 128)
        with self.assertRaises(ValidationError):
            validate_password('x' * 129)
        with self.assertRaises(ValidationError):
            validate_password('Sh0rt!')
        with self.assertRaises(ValidationError):
            validate_password('password1')
        self.set(settings.PASSWORD_MIN_LENGTH, 15)
        with self.assertRaises(ValidationError):
            validate_password('only-12-chars')
        self.set(settings.PASSWORD_MIN_LENGTH, 4)  # never below NIST's 8
        with self.assertRaises(ValidationError):
            validate_password('abc12')

    def test_sign_up_page_shows_the_rules_and_the_meter(self):
        self.set(settings.PASSWORD_MIN_LENGTH, 12)
        page = self.client.get('/account/local/register/').content.decode()
        self.assertIn('<li data-rule="length">At least 12 characters</li>', page)
        self.assertIn('data-rule="similar"', page)
        self.assertRegex(page, r'<input[^>]+data-password-meter[^>]*>')
        self.assertIn('data-min-length="12"', page)
        self.assertIn('maxlength="128"', page)
        self.assertIn('autocomplete="new-password"', page)
        self.assertIn('class="password-meter"', page)
        self.assertIn('osqa.password.js', page)
        self.assertNotIn('onpaste', page)

    def test_breach_check_is_off_by_default(self):
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('no request')):
            from django.contrib.auth.password_validation import validate_password
            validate_password('green-tree-river-7')

    def test_breach_check_sends_only_a_hash_prefix_and_fails_open(self):
        import hashlib
        self.set(settings.PASSWORD_BREACH_CHECK, True)
        password = 'green-tree-river-7'
        digest = hashlib.sha1(password.encode()).hexdigest().upper()
        requests = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def found(request, timeout):
            requests.append((request.full_url, dict(request.header_items()), timeout))
            return Answer(('0018A45C4D1DEF81644B54AB7F969B88D65:1\r\n%s:42\r\n' % digest[5:]).encode())

        with mock.patch('urllib.request.urlopen', found):
            self.assertEqual(passwords.breach_count(password), 42)
            page = self.register(password=password)
        self.assertContains(page, 'appeared in a data breach')
        url, headers, timeout = requests[0]
        self.assertEqual(url, 'https://api.pwnedpasswords.com/range/' + digest[:5])
        self.assertNotIn(password, url + str(headers))
        self.assertNotIn(digest[5:], url + str(headers))
        self.assertEqual(headers.get('Add-padding'), 'true')
        self.assertLessEqual(timeout, 3)

        with mock.patch('urllib.request.urlopen', side_effect=OSError('down')):
            self.assertIsNone(passwords.breach_count(password))
            self.assertEqual(self.register(password=password).status_code, 302)  # accepted


class PasswordChangeTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        self.url = reverse('user_authsettings', kwargs={'id': self.carol.id})

    def test_needs_the_current_password_and_signs_out_other_sessions(self):
        other = self.other_session(self.carol)
        self.sign_in()
        page = self.client.post(self.url, {'oldpw': 'wrong', 'password1': 'new-long-secret-9',
                                           'password2': 'new-long-secret-9'})
        self.assertContains(page, 'your current password')
        self.client.post(self.url, {'oldpw': PASSWORD, 'password1': 'new-long-secret-9',
                                    'password2': 'new-long-secret-9'})
        carol = User.objects.get(id=self.carol.id)
        self.assertTrue(carol.check_password('new-long-secret-9'))
        self.assertTrue(self.still_signed_in(self.client))
        self.assertFalse(self.still_signed_in(other))
        notice = self.mails('password was changed')
        self.assertEqual(len(notice), 1)
        self.assertIn('Every other browser and device was signed out.', notice[0][3])
        self.assertNotIn('new-long-secret-9', notice[0][2] + notice[0][3])

    def test_wrong_current_passwords_are_throttled(self):
        self.sign_in()
        for _i in range(int(settings.LOGIN_FAILURES_LIMIT)):
            self.client.post(self.url, {'oldpw': 'wrong', 'password1': 'new-long-secret-9',
                                        'password2': 'new-long-secret-9'})
        page = self.client.post(self.url, {'oldpw': PASSWORD, 'password1': 'new-long-secret-9',
                                           'password2': 'new-long-secret-9'})
        self.assertContains(page, 'Too many wrong passwords')
        self.assertTrue(User.objects.get(id=self.carol.id).check_password(PASSWORD))

    def test_admin_setting_a_password_signs_the_member_out(self):
        member = self.other_session(self.carol)
        self.login(self.owner)
        self.client.post(self.url, {'password1': 'new-long-secret-9', 'password2': 'new-long-secret-9'})
        self.assertFalse(self.still_signed_in(member))
        self.assertIn('An administrator set a new password', self.mails('password was changed')[0][3])


class PasswordResetTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)

    def request_link(self, email='carol@example.com', client=None):
        self.sent_mail.clear()
        page = (client or self.client).post('/account/tempsignin/', {'email': email})
        return page, (re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[0][3]).group(0)
                      if self.sent_mail else None)

    def reset(self, link, password='new-long-secret-9'):
        return self.client.post(link, {'password1': password, 'password2': password})

    def test_full_reset(self):
        other = self.other_session(self.carol)
        page, link = self.request_link('CAROL@example.com')
        self.assertContains(page, 'Check your inbox')
        mail = self.sent_mail[0]
        self.assertEqual(mail[1], 'Reset your password for OSQA')
        self.assertIn('The link works once, for 60 minutes.', mail[3])
        row = ValidationHash.objects.get(user=self.carol, type='pwreset')
        self.assertLess(row.expiration, datetime.datetime.now() + datetime.timedelta(minutes=61))

        page = self.client.get(link)
        self.assertContains(page, 'autocomplete="new-password"')
        self.assertContains(page, 'name="referrer" content="same-origin"')
        self.assertTrue(User.objects.get(id=self.carol.id).check_password(PASSWORD))  # opening changed nothing

        self.assertContains(self.reset(link, 'short'), 'too short')
        response = self.reset(link)
        self.assertRedirects(response, '/account/signin/', fetch_redirect_response=False)
        self.assertTrue(User.objects.get(id=self.carol.id).check_password('new-long-secret-9'))
        self.assertFalse(self.still_signed_in(other))
        self.assertIsNone(self.signed_in_id())
        self.assertContains(self.client.get('/account/signin/'), 'value="carol"')
        self.assertIn('was reset with the link we emailed', self.mails('password was changed')[0][3])
        self.assertEqual(self.client.get(link).status_code, 410)

    def test_link_expires_and_dies_with_a_password_change_or_sign_in(self):
        _page, link = self.request_link()
        ValidationHash.objects.filter(user=self.carol).update(expiration=datetime.datetime.now() - datetime.timedelta(1))
        self.assertContains(self.client.get(link), 'This link doesn', status_code=410)

        _page, link = self.request_link()
        carol = User.objects.get(id=self.carol.id)
        carol.set_password('changed-elsewhere-1')
        carol.save()
        self.assertEqual(self.client.get(link).status_code, 410)

        _page, link = self.request_link()
        self.sign_in(password='changed-elsewhere-1', client=Client())
        self.assertEqual(self.client.get(link).status_code, 410)

    def test_no_enumeration_and_limits_per_address_and_ip(self):
        known, _link = self.request_link()
        unknown, _none = self.request_link('nobody@example.com')
        card = lambda r: re.search(r'<div class="auth-card".*?</p>', r.content.decode(), re.S).group(0)
        self.assertEqual(card(known), card(unknown))

        for _i in range(2):
            self.request_link()
        page, link = self.request_link()
        self.assertIsNone(link)
        self.assertContains(page, 'Too many password reset emails')
        # One IP address asking for many addresses.
        for i in range(12):
            self.client.post('/account/tempsignin/', {'email': 'someone%d@example.com' % i})
        self.sent_mail.clear()
        dave = self.create_user('dave')
        self.client.post('/account/tempsignin/', {'email': dave.email})
        self.assertEqual(self.sent_mail, [])

    def test_reset_lifts_the_sign_in_pause(self):
        for _i in range(int(settings.LOGIN_FAILURES_LIMIT)):
            self.sign_in(password='wrong')
        _page, link = self.request_link()
        self.reset(link)
        self.sign_in(password='new-long-secret-9')
        self.assertEqual(self.signed_in_id(), str(self.carol.id))

    def test_suspended_accounts_get_nothing(self):
        User.objects.filter(id=self.carol.id).update(is_active=False)
        self.client.post('/account/tempsignin/', {'email': 'carol@example.com'})
        self.assertEqual(self.sent_mail, [])


class SessionTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)

    def test_sign_out_is_a_post_with_csrf(self):
        self.sign_in()
        self.assertContains(self.client.get('/account/signout/'), 'id="btLogout"')
        self.assertContains(self.client.get('/logout/'), 'method="post" action="/account/signout/"')
        self.assertEqual(self.signed_in_id(), str(self.carol.id))

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.carol)
        self.assertEqual(strict.post('/account/signout/').status_code, 403)
        self.assertTrue(self.still_signed_in(strict))

        response = self.client.post('/account/signout/', {'next': '/tags/'})
        self.assertEqual(response['Location'], '/tags/')
        self.assertIsNone(self.signed_in_id())
        self.assertNotIn('evil', self.client.post('/account/signout/', {'next': 'https://evil.example/'})['Location'])

    def test_sign_out_everywhere(self):
        other = self.other_session(self.carol)
        self.sign_in()
        self.client.post('/account/signout/', {'everywhere': '1'})
        self.assertFalse(self.still_signed_in(other))
        self.assertEqual(User.objects.get(id=self.carol.id).session_epoch, 1)

    def test_sign_out_other_sessions(self):
        other = self.other_session(self.carol)
        self.sign_in()
        page = self.client.get(reverse('user_security', kwargs={'id': self.carol.id}))
        self.assertContains(page, reverse('auth_signout_others'))
        self.assertEqual(self.client.get(reverse('auth_signout_others')).status_code, 405)
        with self.assertLogs('forum.security', 'INFO') as logs:
            self.client.post(reverse('auth_signout_others'))
        self.assertTrue(any('event=signout_other_sessions' in line for line in logs.output))
        self.assertTrue(self.still_signed_in(self.client))
        self.assertFalse(self.still_signed_in(other))

    def test_session_key_changes_on_sign_in(self):
        self.client.get('/account/signin/')
        before = self.client.session.session_key
        self.sign_in()
        self.assertNotEqual(self.client.session.session_key, before)

    def test_keep_me_signed_in(self):
        self.assertContains(self.client.get('/account/signin/'), 'name="remember" value="1" checked')
        self.sign_in()
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.client.post('/account/signout/')
        self.sign_in(remember=None)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_new_browser_email(self):
        self.sign_in()
        self.assertEqual(self.mails('New sign-in'), [])  # off by default
        self.assertNotIn(accounts.DEVICE_COOKIE, self.client.cookies)

        self.set(settings.NEW_DEVICE_SIGNIN_EMAIL, True)
        laptop, phone = Client(), Client(HTTP_USER_AGENT='Mozilla/5.0 (Android 14) Chrome/130.0 Mobile')
        self.sign_in(client=laptop)  # the first browser is only remembered
        self.assertEqual(self.mails('New sign-in'), [])
        cookie = laptop.cookies[accounts.DEVICE_COOKIE]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        laptop.post('/account/signout/')
        self.sign_in(client=laptop)
        self.assertEqual(self.mails('New sign-in'), [])
        self.sign_in(client=phone)
        mail = self.mails('New sign-in')
        self.assertEqual(len(mail), 1)
        self.assertIn('Chrome on Android', mail[0][3])
        self.assertIn('127.0.0.1', mail[0][3])


class EmailChangeTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        self.edit_url = reverse('edit_user', kwargs={'id': self.carol.id, 'slug': 'carol'})

    def change(self, email):
        return self.client.post(self.edit_url, {'email': email, 'realname': '', 'website': '', 'city': '',
                                                'birthday': '', 'about': '', 'username': 'carol'})

    def test_confirm_the_new_address(self):
        self.sign_in()
        self.change('carol.new@example.com')
        carol = User.objects.get(id=self.carol.id)
        self.assertEqual(carol.email, 'carol@example.com')
        self.assertEqual(accounts.pending_email(carol), 'carol.new@example.com')
        page = self.client.get(self.edit_url)
        self.assertContains(page, 'Confirm your new email address')
        self.assertContains(page, 'carol.new@example.com')

        confirm = self.mails('Confirm your new email address', 'carol.new@example.com')
        notice = self.mails('is being changed', 'carol@example.com')
        self.assertEqual((len(confirm), len(notice)), (1, 1))
        self.assertIn('c•••@example.com', notice[0][3])
        self.assertNotIn('carol.new@example.com', notice[0][2] + notice[0][3])

        self.client.logout()  # the link works signed out
        page = self.client.get(self.link(confirm[0], 'email/confirm'))
        self.assertContains(page, 'Email address changed')
        carol = User.objects.get(id=self.carol.id)
        self.assertEqual(carol.email, 'carol.new@example.com')
        self.assertTrue(carol.email_isvalid)
        self.assertIsNone(accounts.pending_email(carol))
        self.assertEqual(self.client.get(self.link(confirm[0], 'email/confirm')).status_code, 410)
        self.assertEqual(self.client.get(self.link(notice[0], 'email/cancel')).status_code, 410)

    def test_cancel_from_the_old_address_signs_out_everywhere(self):
        intruder = self.other_session(self.carol)
        intruder.post(self.edit_url, {'email': 'evil@example.com', 'realname': '', 'website': '', 'city': '',
                                      'birthday': '', 'about': ''})
        notice = self.mails('is being changed', 'carol@example.com')[0]
        cancel = self.link(notice, 'email/cancel')
        page = self.client.get(cancel)  # asks first: mail scanners open links
        self.assertContains(page, 'Cancel the change and sign out everywhere')
        self.assertEqual(accounts.pending_email(User.objects.get(id=self.carol.id)), 'evil@example.com')
        page = self.client.post(cancel)
        self.assertContains(page, 'Email change cancelled')
        carol = User.objects.get(id=self.carol.id)
        self.assertIsNone(accounts.pending_email(carol))
        self.assertEqual(carol.email, 'carol@example.com')
        self.assertFalse(self.still_signed_in(intruder))
        confirm = self.mails('Confirm your new email address', 'evil@example.com')[0]
        self.assertEqual(self.client.get(self.link(confirm, 'email/confirm')).status_code, 410)

    def test_resend_and_keep_the_current_address(self):
        self.sign_in()
        self.change('carol.new@example.com')
        self.client.post(reverse('auth_pending_email'), {'action': 'resend'})
        self.assertEqual(len(self.mails('Confirm your new email address')), 2)
        self.client.post(reverse('auth_pending_email'), {'action': 'cancel'})
        self.assertIsNone(accounts.pending_email(User.objects.get(id=self.carol.id)))
        self.assertEqual(self.client.get(reverse('auth_pending_email')).status_code, 405)

    def test_address_taken_in_the_meantime(self):
        self.sign_in()
        self.change('shared@example.com')
        self.create_user('dave', email_isvalid=True)
        User.objects.filter(username='dave').update(email='shared@example.com')
        confirm = self.mails('Confirm your new email address')[0]
        self.assertContains(self.client.get(self.link(confirm, 'email/confirm')), 'already in use')
        self.assertEqual(User.objects.get(id=self.carol.id).email, 'carol@example.com')

    def test_unconfirmed_old_addresses_get_no_notice(self):
        User.objects.filter(id=self.carol.id).update(email_isvalid=False)
        self.sign_in()
        self.change('carol.new@example.com')
        self.assertEqual(self.mails('is being changed'), [])
        self.assertEqual(len(self.mails('Confirm your new email address')), 1)

    def test_admin_change_tells_the_old_address(self):
        self.login(self.owner)
        self.change('carol.other@example.com')
        self.assertEqual(User.objects.get(id=self.carol.id).email, 'carol.other@example.com')
        notice = self.mails('email address was changed', 'carol@example.com')
        self.assertEqual(len(notice), 1)
        self.assertIn('An administrator', notice[0][3])


class AccountDeletionTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True, real_name='Carol C',
                                      about='About me', website='http://carol.example')
        self.url = reverse('user_delete_account', kwargs={'id': self.carol.id})
        self.data_url = reverse('user_account_data', kwargs={'id': self.carol.id})

    def delete(self, **data):
        return self.client.post(self.url, dict({'confirm_username': 'carol', 'password': PASSWORD}, **data),
                                follow=True)

    def personal_data(self, user):
        tag = Tag.objects.create(name='collect', created_by=self.owner)
        question = Question(author=user, title='Carol asks something here', body='Body text', tagnames='collect')
        question.save()
        Answer(author=self.owner, parent=question, body='An answer from the owner').save()
        AuthKeyUserAssociation.objects.create(user=user, key='google:1', provider='google')
        MarkedTag.objects.create(user=user, tag=tag, reason='good')
        QuestionSubscription.objects.create(user=user, question=question)
        Passkey.objects.create(user=user, name='Phone', credential_id='x', credential_hash='h' * 64,
                               public_key='k', user_handle='u')
        two_factor.disable(user)
        PushSubscription.objects.create(user=user, endpoint='https://push.example/1', endpoint_hash='e' * 64,
                                        p256dh='p', auth='a')
        Notification.objects.create(user=user, kind='answer', node=question)
        PendingEmail.objects.create(user=user, kind='answer', node=question)
        user.prop.preferences = {'theme': 'dark'}
        ValidationHash.objects.create_new(user, 'pwreset', [1])
        return question

    def test_deletes_personal_data_keeps_posts_and_emails(self):
        question = self.personal_data(self.carol)
        other = self.other_session(self.carol)
        self.sign_in()
        page = self.client.get(self.data_url)
        self.assertContains(page, 'deleted-user')
        self.assertContains(page, 'We send a last email')
        with self.assertLogs('forum.security', 'INFO') as logs:
            page = self.delete()
        self.assertContains(page, 'Your account has been deleted')
        self.assertIsNone(self.signed_in_id())
        self.assertFalse(self.still_signed_in(other))

        carol = User.objects.get(id=self.carol.id)
        self.assertEqual(carol.username, 'deleted-user-%d' % carol.id)
        self.assertEqual((carol.email, carol.real_name, carol.about, carol.website), ('', '', '', ''))
        self.assertFalse(carol.is_active)
        self.assertFalse(carol.has_usable_password())
        for model in (AuthKeyUserAssociation, MarkedTag, QuestionSubscription, Passkey, TOTPDevice, RecoveryCode,
                      PushSubscription, Notification, PendingEmail, UserProperty, ValidationHash):
            self.assertFalse(model.objects.filter(user=carol).exists(), model.__name__)
        self.assertEqual(Question.objects.get(id=question.id).author_id, carol.id)
        self.assertFalse(Question.objects.get(id=question.id).nis.deleted)

        last = self.mails('account has been deleted')
        self.assertEqual(len(last), 1)
        self.assertEqual(last[0][0].email, 'carol@example.com')
        self.assertIn('show “deleted-user” instead of your name', last[0][3])
        line = [entry for entry in logs.output if 'event=account_deleted' in entry][0]
        self.assertNotIn('carol', line)
        self.assertIn('account_id=%d' % carol.id, line)
        # Can't sign in any more, and can't ask for a reset link.
        self.sign_in()
        self.assertIsNone(self.signed_in_id())

    def test_posts_can_be_deleted_too(self):
        self.set(settings.ACCOUNT_DELETION_POSTS, 'delete')
        question = self.personal_data(self.carol)
        self.sign_in()
        self.assertContains(self.client.get(self.data_url), 'Your questions, answers and comments are deleted.')
        self.delete()
        self.assertTrue(Question.objects.get(id=question.id).nis.deleted)
        self.assertIn('were deleted', self.mails('account has been deleted')[0][3])

    def test_needs_username_and_password(self):
        self.sign_in()
        self.assertContains(self.delete(confirm_username='Carol'), 'Type your username exactly')
        self.assertContains(self.delete(password='wrong'), 'The password is not correct')
        self.assertTrue(User.objects.get(id=self.carol.id).is_active)
        self.assertEqual(self.mails('account has been deleted'), [])

    def test_wrong_passwords_are_throttled(self):
        self.sign_in()
        for _i in range(int(settings.LOGIN_FAILURES_LIMIT)):
            self.delete(password='wrong')
        self.assertContains(self.delete(), 'Too many wrong passwords')
        self.assertTrue(User.objects.get(id=self.carol.id).is_active)

    def test_two_step_code_is_needed_too(self):
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=self.carol, secret=two_factor.encrypt_secret(raw),
                                  confirmed_at='2026-01-01 00:00:00')
        self.login(self.carol)
        self.assertContains(self.client.get(self.data_url), 'name="code"')
        self.assertContains(self.delete(code='000000'), 'authenticator app is not right')
        self.assertTrue(User.objects.get(id=self.carol.id).is_active)
        self.delete(code=two_factor.totp(raw))
        self.assertFalse(User.objects.get(id=self.carol.id).is_active)

    def test_accounts_without_password_sign_in_again_first(self):
        self.carol.set_unusable_password()
        self.carol.save()
        self.login(self.carol, recent=False)  # no recent sign-in time in this session
        page = self.client.get(self.data_url)
        self.assertContains(page, 'sign in again first')
        self.assertNotContains(page, 'name="confirm_username"')
        self.assertContains(self.delete(), 'sign in again')
        self.assertTrue(User.objects.get(id=self.carol.id).is_active)
        session = self.client.session
        session[accounts.AUTH_TIME_SESSION_KEY] = datetime.datetime.now().isoformat()
        session.save()
        self.delete()
        self.assertFalse(User.objects.get(id=self.carol.id).is_active)

    def test_staff_and_the_last_admin_cannot(self):
        self.login(self.owner)
        url = reverse('user_delete_account', kwargs={'id': self.owner.id})
        page = self.client.post(url, {'confirm_username': 'owner', 'password': 'secret-pass'}, follow=True)
        self.assertContains(page, 'only administrator')
        moderator = self.create_user('mod', password=PASSWORD, is_staff=True)
        self.login(moderator)
        self.assertContains(self.client.get(reverse('user_account_data', kwargs={'id': moderator.id})),
                            'Moderators and administrators can')
        page = self.client.post(reverse('user_delete_account', kwargs={'id': moderator.id}),
                                {'confirm_username': 'mod', 'password': PASSWORD}, follow=True)
        self.assertContains(page, 'Ask an administrator')
        self.assertTrue(User.objects.get(id=moderator.id).is_active)

    def test_only_your_own_account(self):
        self.login(self.create_user('mallory'))
        self.assertEqual(self.client.post(self.url, {'confirm_username': 'carol'}).status_code, 404)
