"""Regression tests for the findings of the security review of the account,
two-step, API and upload changes (each test names the issue it guards)."""
import datetime
import json
import re
from unittest import mock
from urllib.parse import urlencode

from django.test import Client
from django.urls import reverse

from forum.models import Action, ApiToken, Passkey, RecoveryCode, TOTPDevice, User, ValidationHash
from forum.utils import accounts, two_factor

from .test_account_lifecycle import PASSWORD, AccountTestCase

MARKUP = '<img src=x onerror=alert(1)>'


class FlashMessageEscapingTest(AccountTestCase):
    """Flash messages are rendered unescaped (base_content.html): member text in them must be escaped."""

    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)

    def passkey(self, user):
        return Passkey.objects.create(user=user, name=MARKUP, credential_id='c%d' % user.id,
                                      credential_hash=('%d' % user.id) * 64, public_key='k', user_handle='u')

    def test_passkey_name_in_the_removal_message_is_escaped(self):
        for remover in (self.carol, self.owner):  # the member, and an administrator removing it for them
            key = self.passkey(self.carol)
            self.login(remover)
            page = self.client.post(reverse('passkey_remove', kwargs={'id': key.id}), follow=True)
            self.assertNotContains(page, MARKUP)
            self.assertContains(page, '&lt;img src=x onerror=alert(1)&gt;')
            self.assertFalse(Passkey.objects.filter(id=key.id).exists())


class PasswordResetCodeTest(AccountTestCase):
    """A reset form sent with a password mistake used up the two-step code (and a recovery code) for nothing."""

    def test_a_password_mistake_does_not_use_up_the_recovery_code(self):
        carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=carol, secret=two_factor.encrypt_secret(raw), confirmed_at='2026-01-01 00:00:00')
        codes = two_factor.new_recovery_codes(carol)
        self.client.post('/account/tempsignin/', {'email': carol.email})
        link = re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[-1][3]).group(0)

        page = self.client.post(link, {'password1': 'new-long-secret-9', 'password2': 'typo-long-secret-9',
                                       'code': codes[0]})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(RecoveryCode.objects.filter(user=carol, used_at__isnull=True).count(), len(codes))
        self.assertTrue(User.objects.get(id=carol.id).check_password(PASSWORD))

        # The same recovery code then works with the corrected form.
        self.client.post(link, {'password1': 'new-long-secret-9', 'password2': 'new-long-secret-9', 'code': codes[0]})
        self.assertTrue(User.objects.get(id=carol.id).check_password('new-long-secret-9'))


class OneTimeLinkRaceTest(AccountTestCase):
    """validate(consume=True) returned True even when another request had used the link in the meantime."""

    def test_a_link_used_by_a_concurrent_request_is_refused(self):
        carol = self.create_user('carol', password=PASSWORD)
        token = ValidationHash.objects.create_new(carol, 'pwreset', ['x']).token
        stale = ValidationHash.objects.get(user=carol, type='pwreset')
        ValidationHash.objects.filter(pk=stale.pk).delete()  # the other request consumed it
        with mock.patch.object(type(ValidationHash.objects), '_find', return_value=stale):
            self.assertFalse(ValidationHash.objects.validate(token, carol, 'pwreset', ['x']))
        token = ValidationHash.objects.create_new(carol, 'pwreset', ['x']).token
        self.assertTrue(ValidationHash.objects.validate(token, carol, 'pwreset', ['x']))
        self.assertFalse(ValidationHash.objects.validate(token, carol, 'pwreset', ['x']))


class AccountDeletionIPTest(AccountTestCase):
    """Deleting an account left the IP address of every sign-in, post and vote in forum_action."""

    def test_deleted_accounts_keep_no_ip_addresses(self):
        carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        self.sign_in()
        self.assertTrue(Action.objects.filter(user=carol).exclude(ip='').exists())
        self.client.post(reverse('user_delete_account', kwargs={'id': carol.id}),
                         {'confirm_username': 'carol', 'password': PASSWORD})
        self.assertFalse(User.objects.get(id=carol.id).is_active)
        self.assertFalse(Action.objects.filter(user=carol).exclude(ip='').exists())
        self.assertTrue(Action.objects.filter(user=carol).exists())  # the history itself stays


class EmailChangeHardeningTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        self.edit_url = reverse('edit_user', kwargs={'id': self.carol.id, 'slug': 'carol'})

    def change(self, client, email):
        return client.post(self.edit_url, {'email': email, 'realname': '', 'website': '', 'city': '',
                                           'birthday': '', 'about': '', 'username': 'carol'})

    def link(self, words, kind):
        mail = self.mails(words)[-1]
        return re.search(r'http://testserver(/account/%s/\d+/[^/\s]+/)' % kind, mail[3]).group(1)

    def test_reset_links_sent_to_the_old_address_die_with_the_change(self):
        session = self.other_session(self.carol)  # signed in before the reset link was sent
        Client().post('/account/tempsignin/', {'email': 'carol@example.com'})
        reset = re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.mails('Reset your password')[-1][3]).group(0)
        self.change(session, 'carol.new@example.com')
        self.client.get(self.link('Confirm your new email address', 'email/confirm'))
        self.assertEqual(User.objects.get(id=self.carol.id).email, 'carol.new@example.com')
        self.assertEqual(Client().get(reset).status_code, 410)

    def test_cancelling_from_the_old_address_revokes_api_tokens(self):
        intruder = self.other_session(self.carol)
        _token, raw = ApiToken.objects.create_token(self.carol, 'intruder', ('read', 'write'))
        self.change(intruder, 'evil@example.com')
        self.client.post(self.link('is being changed', 'email/cancel'))
        self.assertIsNone(accounts.pending_email(User.objects.get(id=self.carol.id)))
        self.assertFalse(ApiToken.objects.filter(user=self.carol).exists())
        self.assertEqual(Client().get('/api/v1/me', HTTP_AUTHORIZATION='Bearer %s' % raw).status_code, 401)


# -- "Confirm it's you" (forum/utils/sudo.py) ---------------------------------------------

class SudoTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)
        self.security = reverse('user_security', kwargs={'id': self.carol.id})
        self.tokens = reverse('user_api_tokens', kwargs={'id': self.carol.id})

    def confirm_url(self, next_url):
        return '/account/confirm/?' + urlencode({'next': next_url})

    def test_an_old_sign_in_is_sent_to_confirm_and_back(self):
        self.login(self.carol, recent=False)
        response = self.client.get('/account/security/two-step/')
        self.assertRedirects(response, self.confirm_url('/account/security/two-step/'), fetch_redirect_response=False)
        page = self.client.get(response['Location'])
        self.assertContains(page, "<h1>Confirm it's you</h1>", html=False)
        self.assertContains(page, 'autocomplete="current-password"')
        self.assertNotContains(page, 'name="code"')  # no two-step verification: no code field

        with self.assertLogs('forum.security', 'INFO'):
            page = self.client.post('/account/confirm/', {'method': 'password', 'password': 'wrong',
                                                          'next': '/account/security/two-step/'})
        self.assertContains(page, 'That password is not right')
        response = self.client.post('/account/confirm/', {'method': 'password', 'password': PASSWORD,
                                                          'next': '/account/security/two-step/'})
        self.assertRedirects(response, '/account/security/two-step/', fetch_redirect_response=False)
        self.assertContains(self.client.get('/account/security/two-step/'), 'QR code')

    def test_wrong_passwords_use_the_sign_in_pause(self):
        self.login(self.carol, recent=False)
        for _i in range(int(settings_module().LOGIN_FAILURES_LIMIT)):
            self.client.post('/account/confirm/', {'method': 'password', 'password': 'wrong'})
        page = self.client.post('/account/confirm/', {'method': 'password', 'password': PASSWORD})
        self.assertContains(page, 'Too many wrong passwords')

    def test_next_must_be_on_this_site(self):
        self.login(self.carol, recent=False)
        for target in ('https://evil.example/', '//evil.example/x', '/\\evil.example'):
            response = self.client.post('/account/confirm/', {'method': 'password', 'password': PASSWORD,
                                                              'next': target})
            self.assertRedirects(response, self.security, fetch_redirect_response=False)

    def test_a_recent_sign_in_is_enough(self):
        self.sign_in()
        self.assertEqual(self.client.get('/account/security/two-step/').status_code, 200)
        with mock.patch('forum.utils.accounts._now',
                        return_value=datetime.datetime.now() + datetime.timedelta(minutes=16)):
            response = self.client.get('/account/security/two-step/')
        self.assertTrue(response['Location'].startswith('/account/confirm/'))

    def test_email_change_waits_and_saves_nothing(self):
        self.login(self.carol, recent=False)
        edit = reverse('edit_user', kwargs={'id': self.carol.id, 'slug': 'carol'})
        response = self.client.post(edit, {'email': 'evil@example.com', 'realname': 'Changed', 'website': '',
                                           'city': '', 'birthday': '', 'about': '', 'username': 'carol'})
        self.assertRedirects(response, self.confirm_url(edit), fetch_redirect_response=False)
        carol = User.objects.get(id=self.carol.id)
        self.assertIsNone(accounts.pending_email(carol))
        self.assertEqual(carol.real_name, '')
        self.assertEqual(self.sent_mail, [])
        # Other profile changes don't ask.
        self.client.post(edit, {'email': 'carol@example.com', 'realname': 'Carol', 'website': '', 'city': '',
                                'birthday': '', 'about': '', 'username': 'carol'})
        self.assertEqual(User.objects.get(id=self.carol.id).real_name, 'Carol')

    def test_api_tokens_and_passkeys_ask_first(self):
        self.login(self.carol, recent=False)
        response = self.client.post(self.tokens, {'name': 'x', 'access': 'read', 'expires': '30'})
        self.assertRedirects(response, self.confirm_url(self.tokens), fetch_redirect_response=False)
        self.assertFalse(ApiToken.objects.filter(user=self.carol).exists())
        self.assertEqual(self.client.get(self.tokens).status_code, 200)  # looking needs nothing
        response = self.client.post('/account/passkeys/register/options/', '{}', content_type='application/json')
        self.assertNotIn('challenge', response.json())
        self.assertEqual(response.json()['redirect'], self.confirm_url(self.security))

    def test_two_step_code_confirms_and_is_throttled(self):
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=self.carol, secret=two_factor.encrypt_secret(raw),
                                  confirmed_at='2026-01-01 00:00:00')
        self.login(self.carol, recent=False)
        self.assertContains(self.client.get('/account/confirm/'), 'name="code"')
        for _i in range(int(settings_module().LOGIN_FAILURES_LIMIT)):
            self.client.post('/account/confirm/', {'method': 'code', 'code': '000000'})
        page = self.client.post('/account/confirm/', {'method': 'code', 'code': two_factor.totp(raw)})
        self.assertContains(page, 'Too many wrong codes')

    def test_the_code_confirms(self):
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=self.carol, secret=two_factor.encrypt_secret(raw),
                                  confirmed_at='2026-01-01 00:00:00')
        self.login(self.carol, recent=False)
        response = self.client.post('/account/confirm/', {'method': 'code', 'code': two_factor.totp(raw),
                                                          'next': self.tokens})
        self.assertRedirects(response, self.tokens, fetch_redirect_response=False)
        self.assertTrue(accounts.recently_signed_in(response.wsgi_request))

    def test_members_without_password_or_two_step_get_a_link(self):
        self.carol.set_unusable_password()
        self.carol.save()
        self.login(self.carol, recent=False)
        page = self.client.get(self.confirm_url(self.tokens))
        self.assertContains(page, 'Email me a link')
        self.assertNotContains(page, 'name="password"')
        page = self.client.post('/account/confirm/', {'method': 'email', 'next': self.tokens})
        self.assertContains(page, 'We emailed you a link')
        mail = self.mails('Confirm it')[-1]
        link = re.search(r'http://testserver(/account/confirm/\d+/[^/\s]+/)', mail[3]).group(1)
        # Opened elsewhere (a mail scanner, another browser): refused and not used up.
        self.assertEqual(Client().get(link).status_code, 403)
        response = self.client.get(link)
        self.assertRedirects(response, self.tokens, fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.tokens, {'name': 'x', 'access': 'read', 'expires': '30'}).status_code,
                         200)
        self.assertEqual(self.client.get(link).status_code, 410)  # single use
        row_types = set(ValidationHash.objects.filter(user=self.carol).values_list('type', flat=True))
        self.assertNotIn('reauth', row_types)

    def test_the_link_expires(self):
        self.carol.set_unusable_password()
        self.carol.save()
        self.login(self.carol, recent=False)
        self.client.post('/account/confirm/', {'method': 'email'})
        link = re.search(r'http://testserver(/account/confirm/\d+/[^/\s]+/)', self.mails('Confirm it')[-1][3]).group(1)
        ValidationHash.objects.filter(user=self.carol, type='reauth').update(
            expiration=datetime.datetime.now() - datetime.timedelta(minutes=1))
        self.assertEqual(self.client.get(link).status_code, 410)

    def test_passkey_confirms(self):
        from .test_two_factor import SoftAuthenticator
        authenticator = SoftAuthenticator()
        self.login(self.carol)
        options = self.client.post('/account/passkeys/register/options/', '{}', content_type='application/json').json()
        self.client.post('/account/passkeys/register/', json.dumps({'credential': authenticator.create(options),
                                                                    'name': 'Phone'}),
                         content_type='application/json')
        self.login(self.carol, recent=False)
        self.assertContains(self.client.get('/account/confirm/'), 'Confirm with a passkey')
        options = self.client.post('/account/confirm/passkey/options/', '{}', content_type='application/json').json()
        self.assertEqual([c['id'] for c in options['allowCredentials']], [authenticator.id])
        reply = self.client.post('/account/confirm/passkey/?next=' + self.tokens,
                                 json.dumps({'credential': authenticator.get(options)}), content_type='application/json')
        self.assertEqual(reply.json(), {'redirect': self.tokens})
        self.assertTrue(accounts.recently_signed_in(reply.wsgi_request))

    def test_another_members_passkey_does_not_confirm(self):
        from .test_two_factor import SoftAuthenticator
        dave = self.create_user('dave', password=PASSWORD)
        authenticator = SoftAuthenticator()
        self.login(dave)
        options = self.client.post('/account/passkeys/register/options/', '{}', content_type='application/json').json()
        self.client.post('/account/passkeys/register/', json.dumps({'credential': authenticator.create(options)}),
                         content_type='application/json')
        Passkey.objects.create(user=self.carol, name='Carol key', credential_id='b3RoZXI', credential_hash='c' * 64,
                               public_key='k', user_handle='u')
        self.login(self.carol, recent=False)
        options = self.client.post('/account/confirm/passkey/options/', '{}', content_type='application/json').json()
        reply = self.client.post('/account/confirm/passkey/', json.dumps({'credential': authenticator.get(options)}),
                                 content_type='application/json')
        self.assertEqual(reply.status_code, 400)
        self.assertFalse(accounts.recently_signed_in(reply.wsgi_request))

    def test_removing_the_last_sign_in_method_asks(self):
        self.carol.set_unusable_password()
        self.carol.save()
        key = Passkey.objects.create(user=self.carol, name='Only', credential_id='only', credential_hash='o' * 64,
                                     public_key='k', user_handle='u')
        self.login(self.carol, recent=False)
        response = self.client.post(reverse('passkey_remove', kwargs={'id': key.id}))
        self.assertRedirects(response, self.confirm_url(self.security), fetch_redirect_response=False)
        self.assertTrue(Passkey.objects.filter(id=key.id).exists())
        # Not the last one (a sign-in service is left): no question.
        from forum.models import AuthKeyUserAssociation
        AuthKeyUserAssociation.objects.create(user=self.carol, key='google:9', provider='google')
        self.client.post(reverse('passkey_remove', kwargs={'id': key.id}))
        self.assertFalse(Passkey.objects.filter(id=key.id).exists())
        assoc = AuthKeyUserAssociation.objects.get(user=self.carol)
        response = self.client.post(reverse('user_remove_external_provider', kwargs={'id': assoc.id}))
        self.assertTrue(response['Location'].startswith('/account/confirm/'))
        self.assertTrue(AuthKeyUserAssociation.objects.filter(id=assoc.id).exists())

    def test_admins_confirm_before_turning_off_a_members_two_step(self):
        TOTPDevice.objects.create(user=self.carol, secret=two_factor.encrypt_secret(two_factor.new_secret()),
                                  confirmed_at='2026-01-01 00:00:00')
        self.login(self.owner, recent=False)
        url = reverse('two_factor_disable', kwargs={'id': self.carol.id})
        self.assertTrue(self.client.post(url)['Location'].startswith('/account/confirm/'))
        self.assertTrue(two_factor.is_enabled(self.carol))
        self.login(self.owner)
        self.client.post(url)
        self.assertFalse(two_factor.is_enabled(self.carol))
        notice = self.mails('Security change', 'carol@example.com')[-1]
        self.assertIn('An administrator turned off two-step verification', notice[3])

    def test_the_confirm_page_needs_a_sign_in(self):
        self.assertRedirects(self.client.get('/account/confirm/'), '/account/signin/', fetch_redirect_response=False)


def settings_module():
    from forum import settings
    return settings


# -- security notices ------------------------------------------------------------------------

class SecurityNoticeTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.carol = self.create_user('carol', password=PASSWORD, email_isvalid=True)

    def notice(self, words):
        mails = self.mails('Security change', 'carol@example.com')
        self.assertEqual(len(mails), 1, [m[1] for m in self.sent_mail])
        self.assertIn(words, mails[0][3])
        self.assertIn('/account/%d/security/' % self.carol.id, mails[0][3])
        self.assertIn('/account/tempsignin/', mails[0][3])
        self.sent_mail.clear()

    def test_each_change_is_emailed(self):
        from .test_two_factor import SoftAuthenticator
        self.login(self.carol)
        self.client.get('/account/security/two-step/')
        raw = two_factor.decrypt_secret(TOTPDevice.objects.get(user=self.carol).secret)
        self.client.post('/account/security/two-step/', {'code': two_factor.totp(raw)})
        self.notice('Two-step verification was turned on')

        with mock.patch('forum.utils.two_factor.time.time', return_value=datetime.datetime.now().timestamp() + 60):
            self.client.post(reverse('two_factor_recovery_codes'), {'code': two_factor.totp(raw)})
        self.notice('New recovery codes were made')

        authenticator = SoftAuthenticator()
        options = self.client.post('/account/passkeys/register/options/', '{}', content_type='application/json').json()
        self.client.post('/account/passkeys/register/', json.dumps({'credential': authenticator.create(options),
                                                                    'name': '<b>Laptop</b>'}),
                         content_type='application/json')
        mail = self.mails('Security change', 'carol@example.com')[0]
        self.assertIn('&lt;b&gt;Laptop&lt;/b&gt;', mail[2])  # the HTML part escapes the name
        self.notice('A passkey named')

        self.client.post(reverse('user_api_tokens', kwargs={'id': self.carol.id}),
                         {'name': 'Report script', 'access': 'read', 'expires': '30'})
        self.notice('An API token named “Report script” was created')

        self.client.post(reverse('two_factor_disable', kwargs={'id': self.carol.id}), {'password': PASSWORD})
        self.notice('Two-step verification was turned off for your account')


# -- uploads ------------------------------------------------------------------------------------

class UploadLimitTest(AccountTestCase):
    PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4'
           b'\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82')

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        from forum import settings
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        self.set(settings.UPFILES_FOLDER, folder)
        self.set(settings.UPLOADS_PER_HOUR, 2)

    def upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self.client.post('/upload/', {'file-upload': SimpleUploadedFile('a.png', self.PNG)})

    def test_hourly_limit_per_member_staff_exempt(self):
        self.login(self.create_user('alice', reputation=500))
        self.assertEqual([self.upload().status_code for _i in range(2)], [200, 200])
        reply = self.upload()
        self.assertEqual(reply.status_code, 429)
        self.assertIn('the most you can', reply.json()['error'])
        self.login(self.create_user('bob', reputation=500))  # per member
        self.assertEqual(self.upload().status_code, 200)
        self.login(self.owner)
        self.assertEqual([self.upload().status_code for _i in range(3)], [200, 200, 200])

    def test_absurd_sides_are_refused_before_decoding(self):
        import struct
        import zlib
        from PIL import ImageFile
        from forum.utils import images

        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
        strip = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 40000, 1, 8, 0, 0, 0, 0)) +
                 chunk(b'IDAT', zlib.compress(b'\0' * 40001)) + chunk(b'IEND', b''))
        import io
        with mock.patch.object(ImageFile.ImageFile, 'load', side_effect=AssertionError('decoded')):
            with self.assertRaises(images.ImageTooLarge):
                images.process_image(io.BytesIO(strip), 'strip.png')
