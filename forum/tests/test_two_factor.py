"""Passkeys and two-step verification (forum/utils/two_factor.py,
forum/utils/passkeys.py, forum/views/security.py).

Passkeys are tested end to end with SoftAuthenticator below, which makes real
WebAuthn answers (P-256 key, "none" attestation) that py_webauthn verifies.
"""
import hashlib
import json
import os
import re
import struct
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import override_settings
from webauthn.helpers import bytes_to_base64url

from forum import settings
from forum.models import Passkey, RecoveryCode, TOTPDevice, User
from forum.utils import passkeys, two_factor

from .base import OSQATestCase


class SoftAuthenticator:
    """A passkey provider in software: answers navigator.credentials.create()
    and .get() options the way a browser would send them to the server."""

    def __init__(self, rp_id='testserver', origin='http://testserver'):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.rp_id, self.origin = rp_id, origin
        self.sign_count = 0
        self.user_handle = None

    @property
    def id(self):
        return bytes_to_base64url(self.credential_id)

    def _cose_key(self):
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, 'big'), -3: numbers.y.to_bytes(32, 'big')})

    def _client_data(self, kind, challenge):
        return json.dumps({'type': kind, 'challenge': challenge, 'origin': self.origin,
                           'crossOrigin': False}).encode()

    def _auth_data(self, flags, attested=False):
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack('>I', self.sign_count)
        if attested:
            data += b'\0' * 16 + struct.pack('>H', len(self.credential_id)) + self.credential_id + self._cose_key()
        return data

    def create(self, options):
        self.user_handle = options['user']['id']
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': self._auth_data(0x45, True)})
        return {'id': self.id, 'rawId': self.id, 'type': 'public-key', 'clientExtensionResults': {},
                'response': {'clientDataJSON': bytes_to_base64url(self._client_data('webauthn.create',
                                                                                   options['challenge'])),
                             'attestationObject': bytes_to_base64url(attestation), 'transports': ['internal']}}

    def get(self, options, count=True):
        if count:
            self.sign_count += 1
        auth_data = self._auth_data(0x05)
        client_data = self._client_data('webauthn.get', options['challenge'])
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': self.id, 'rawId': self.id, 'type': 'public-key', 'clientExtensionResults': {},
                'response': {'clientDataJSON': bytes_to_base64url(client_data),
                             'authenticatorData': bytes_to_base64url(auth_data),
                             'signature': bytes_to_base64url(signature), 'userHandle': self.user_handle}}


class TwoFactorTestCase(OSQATestCase):
    def set_setting(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def enable_totp(self, user):
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=user, secret=two_factor.encrypt_secret(raw),
                                  confirmed_at='2026-01-01 00:00:00')
        codes = two_factor.new_recovery_codes(user)
        return raw, codes

    def logged_in_user_id(self):
        return self.client.session.get('_auth_user_id')

    def post_json(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type='application/json')


class TOTPTest(TwoFactorTestCase):
    def test_rfc6238_vector(self):
        raw = b'12345678901234567890'
        # RFC 6238 appendix B, SHA-1, T=59: 94287082 (last six digits for a 6-digit code).
        self.assertEqual(two_factor.totp(raw, now=59), '287082')
        self.assertEqual(two_factor.totp(raw, now=1111111109), '081804')

    def test_one_step_either_side_is_accepted(self):
        raw = two_factor.new_secret()
        now = 1_800_000_000
        for offset, ok in ((-60, False), (-30, True), (0, True), (30, True), (60, False)):
            code = two_factor.totp(raw, now + offset)
            self.assertEqual(two_factor.matching_step(raw, code, 0, now) is not None, ok, offset)
        self.assertIsNone(two_factor.matching_step(raw, 'abcdef', 0, now))
        self.assertIsNone(two_factor.matching_step(raw, '', 0, now))

    def test_codes_are_accepted_once(self):
        user = self.create_user('carol')
        raw, _codes = self.enable_totp(user)
        dev = two_factor.device(user)
        now = time.time()
        code = two_factor.totp(raw, now)
        self.assertTrue(two_factor.use_totp(dev, code, now))
        self.assertFalse(two_factor.use_totp(TOTPDevice.objects.get(pk=dev.pk), code, now))
        # Nor the previous step's code once a later one was used.
        self.assertFalse(two_factor.use_totp(TOTPDevice.objects.get(pk=dev.pk), two_factor.totp(raw, now - 30), now))
        self.assertTrue(two_factor.use_totp(TOTPDevice.objects.get(pk=dev.pk), two_factor.totp(raw, now + 30), now))

    def test_secret_is_encrypted_and_survives_key_rotation(self):
        user = self.create_user('carol')
        raw, _codes = self.enable_totp(user)
        stored = TOTPDevice.objects.get(user=user).secret
        self.assertNotIn(two_factor.secret_text(raw).replace(' ', ''), stored)
        self.assertNotIn(raw.hex(), stored)
        self.assertEqual(two_factor.decrypt_secret(stored), raw)
        from django.conf import settings as djsettings
        with override_settings(SECRET_KEY='a-new-key-' + 'x' * 40, SECRET_KEY_FALLBACKS=[djsettings.SECRET_KEY]):
            self.assertEqual(two_factor.decrypt_secret(stored), raw)
        with override_settings(SECRET_KEY='a-new-key-' + 'x' * 40, SECRET_KEY_FALLBACKS=[]):
            self.assertIsNone(two_factor.decrypt_secret(stored))

    def test_provisioning_uri_and_qr_code(self):
        raw = b'12345678901234567890'
        uri = two_factor.provisioning_uri(raw, 'carol')
        self.assertTrue(uri.startswith('otpauth://totp/'))
        query = parse_qs(urlparse(uri).query)
        self.assertEqual(query['secret'], ['GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'])
        self.assertEqual(query['period'], ['30'])
        svg = two_factor.qr_svg(uri, 'QR')
        self.assertTrue(svg.startswith('<svg'))
        self.assertNotIn('<script', svg)


class RecoveryCodeTest(TwoFactorTestCase):
    def test_ten_hashed_one_time_codes(self):
        user = self.create_user('carol')
        codes = two_factor.new_recovery_codes(user)
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertTrue(all(re.fullmatch(r'[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}', c) for c in codes))
        stored = ' '.join(RecoveryCode.objects.values_list('code_hash', flat=True))
        for code in codes:
            self.assertNotIn(code.replace('-', ''), stored)

        self.assertTrue(two_factor.use_recovery_code(user, codes[0].upper().replace('-', ' ')))
        self.assertFalse(two_factor.use_recovery_code(user, codes[0]))
        self.assertEqual(two_factor.recovery_codes_left(user), 9)
        self.assertFalse(two_factor.use_recovery_code(user, 'aaaa-bbbb-cccc'))

    def test_new_codes_replace_the_old_ones(self):
        user = self.create_user('carol')
        old = two_factor.new_recovery_codes(user)
        new = two_factor.new_recovery_codes(user)
        self.assertFalse(two_factor.use_recovery_code(user, old[1]))
        self.assertTrue(two_factor.use_recovery_code(user, new[1]))


class SetupTest(TwoFactorTestCase):
    def test_setup_confirm_and_disable(self):
        user = self.create_user('carol', password='right-pass')
        self.login(user)
        page = self.client.get('/account/security/two-step/')
        self.assertContains(page, '<svg')
        self.assertIn('no-store', page['Cache-Control'])
        dev = TOTPDevice.objects.get(user=user)
        self.assertIsNone(dev.confirmed_at)
        raw = two_factor.decrypt_secret(dev.secret)
        self.assertContains(page, two_factor.secret_text(raw))
        # Reloading keeps the same secret (the app may already have scanned it).
        self.client.get('/account/security/two-step/')
        self.assertEqual(TOTPDevice.objects.get(user=user).secret, dev.secret)
        self.assertFalse(two_factor.is_enabled(user))

        page = self.client.post('/account/security/two-step/', {'code': '000000' if two_factor.totp(raw) != '000000'
                                                                 else '111111'})
        self.assertContains(page, 'That code is not right')
        self.assertFalse(two_factor.is_enabled(user))

        page = self.client.post('/account/security/two-step/', {'code': two_factor.totp(raw)})
        self.assertTrue(two_factor.is_enabled(user))
        codes = re.findall(r'<code>([a-z2-9-]{14})</code>', page.content.decode())
        self.assertEqual(len(codes), 10)
        self.assertEqual(RecoveryCode.objects.filter(user=user).count(), 10)

        security = self.client.get('/account/%d/security/' % user.id)
        self.assertContains(security, '10 codes left')

        # Turning it off needs a code or the password.
        self.client.post('/account/%d/security/two-step/disable/' % user.id, {'password': 'wrong'})
        self.assertTrue(two_factor.is_enabled(user))
        self.client.post('/account/%d/security/two-step/disable/' % user.id, {'password': 'right-pass'})
        self.assertFalse(two_factor.is_enabled(user))
        self.assertEqual(RecoveryCode.objects.filter(user=user).count(), 0)

    def test_regenerate_recovery_codes_needs_a_code(self):
        user = self.create_user('carol')
        raw, old = self.enable_totp(user)
        self.login(user)
        page = self.client.post('/account/security/recovery-codes/', {'code': '12'})
        self.assertEqual(page.status_code, 302)
        self.assertTrue(two_factor.use_recovery_code(user, old[0]))
        page = self.client.post('/account/security/recovery-codes/', {'code': two_factor.totp(raw)})
        codes = re.findall(r'<code>([a-z2-9-]{14})</code>', page.content.decode())
        self.assertEqual(len(codes), 10)
        self.assertFalse(two_factor.use_recovery_code(user, old[1]))
        self.assertTrue(two_factor.use_recovery_code(user, codes[0]))

    def test_disable_with_a_code(self):
        user = self.create_user('carol')
        raw, _codes = self.enable_totp(user)
        self.login(user)
        self.client.post('/account/%d/security/two-step/disable/' % user.id, {'code': two_factor.totp(raw)})
        self.assertFalse(two_factor.is_enabled(user))

    def test_security_page_access(self):
        carol, dave = self.create_user('carol'), self.create_user('dave')
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.enable_totp(carol)
        self.login(carol)
        self.assertContains(self.client.get('/account/%d/security/' % carol.id), 'Passkeys')
        self.assertNotEqual(self.client.get('/account/%d/security/' % dave.id).status_code, 200)
        self.client.post('/account/%d/security/two-step/disable/' % dave.id)
        self.assertTrue(two_factor.is_enabled(carol))

        # An administrator can turn it off for a member who lost their phone.
        self.login(admin)
        self.assertEqual(self.client.get('/account/%d/security/' % carol.id).status_code, 200)
        self.client.post('/account/%d/security/two-step/disable/' % carol.id)
        self.assertFalse(two_factor.is_enabled(carol))

    def test_security_page_is_linked_from_the_settings_menu(self):
        user = self.create_user('carol')
        self.login(user)
        page = self.client.get('/account/%d/authsettings/' % user.id)
        self.assertContains(page, '/account/%d/security/' % user.id)


class PendingLoginTest(TwoFactorTestCase):
    def test_password_then_code(self):
        user = self.create_user('carol', password='right-pass')
        raw, _codes = self.enable_totp(user)
        self.client.get('/account/signin/?next=/questions/')
        response = self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.assertRedirects(response, '/account/two-step/', fetch_redirect_response=False)
        self.assertIsNone(self.logged_in_user_id())
        marker = self.client.session[two_factor.PENDING_SESSION_KEY]
        self.assertNotIn('right-pass', marker)

        page = self.client.get('/account/two-step/')
        self.assertContains(page, 'one-time-code')
        self.assertIsNone(self.logged_in_user_id())

        page = self.client.post('/account/two-step/', {'code': 'nope'})
        self.assertContains(page, 'That code is not right')
        self.assertIsNone(self.logged_in_user_id())

        response = self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        self.assertTrue(response['Location'].startswith('/questions/'))
        self.assertEqual(self.client.session[two_factor.METHOD_SESSION_KEY], 'totp')
        self.assertNotIn(two_factor.PENDING_SESSION_KEY, self.client.session)

    def test_recovery_code_signs_in_once(self):
        user = self.create_user('carol', password='right-pass')
        _raw, codes = self.enable_totp(user)
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.client.post('/account/two-step/', {'code': codes[3]})
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        self.assertEqual(self.client.session[two_factor.METHOD_SESSION_KEY], 'recovery')

        self.client.logout()
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.client.post('/account/two-step/', {'code': codes[3]})
        self.assertIsNone(self.logged_in_user_id())

    def test_without_pending_sign_in_the_page_sends_to_sign_in(self):
        response = self.client.get('/account/two-step/')
        self.assertRedirects(response, '/account/signin/', fetch_redirect_response=False)
        session = self.client.session
        session[two_factor.PENDING_SESSION_KEY] = 'forged:marker'
        session.save()
        self.assertRedirects(self.client.post('/account/two-step/', {'code': '123456'}), '/account/signin/',
                             fetch_redirect_response=False)

    def test_marker_expires_and_follows_password_changes(self):
        user = self.create_user('carol', password='right-pass')
        raw, _codes = self.enable_totp(user)
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        user.set_password('another-pass')
        user.save()
        self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertIsNone(self.logged_in_user_id())

        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'another-pass'})
        with mock.patch.object(two_factor, 'PENDING_MAX_AGE', -1):
            self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertIsNone(self.logged_in_user_id())

    def test_email_link_sign_in_needs_the_code_too(self):
        user = self.create_user('carol')
        raw, _codes = self.enable_totp(user)
        self.client.post('/account/tempsignin/', {'email': 'carol@example.com'})
        link = re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[0][3]).group(0)
        response = self.client.get(link)
        self.assertRedirects(response, '/account/two-step/', fetch_redirect_response=False)
        self.assertIsNone(self.logged_in_user_id())
        response = self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        # The link's destination (set a new password) is kept.
        self.assertTrue(response['Location'].startswith('/account/%d/authsettings/' % user.id))

    def email_link(self, user):
        self.sent_mail.clear()
        self.client.post('/account/tempsignin/', {'email': user.email})
        return re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[0][3]).group(0)

    def test_email_link_changes_nothing_until_the_code_is_right(self):
        user = self.create_user('carol', password='right-pass')
        raw, _codes = self.enable_totp(user)
        # Opened, but the code step abandoned (or failed): the password still works.
        self.client.get(self.email_link(user))
        self.client.post('/account/two-step/', {'code': 'nope'})
        self.assertIsNone(self.logged_in_user_id())
        self.assertTrue(User.objects.get(id=user.id).check_password('right-pass'))
        self.client.logout()
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertEqual(self.logged_in_user_id(), str(user.id))

        # Completed: as for members without two-step verification, the
        # forgotten password stops working and the member is asked to set one.
        self.client.logout()
        self.client.get(self.email_link(user))
        response = self.client.post('/account/two-step/', {'code': two_factor.totp(raw, time.time() + 30)})
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        self.assertTrue(response['Location'].startswith('/account/%d/authsettings/' % user.id))
        self.assertFalse(User.objects.get(id=user.id).has_usable_password())

    def test_email_link_without_two_step_is_unchanged(self):
        user = self.create_user('dave', password='right-pass')
        self.client.get(self.email_link(user))
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        self.assertFalse(User.objects.get(id=user.id).has_usable_password())

    def test_oauth_sign_in_needs_the_code_too(self):
        import forum.authentication  # noqa: F401
        from forum.models import AuthKeyUserAssociation
        from forum_modules.oauth2auth import authentication as oauth2
        from forum_modules.oauth2auth import settings as oauth2_settings
        from .test_oauth2auth import GOOGLE_PROFILE, FakeProvider
        for name, value in (('GOOGLE_CLIENT_ID', 'google-id'), ('GOOGLE_CLIENT_SECRET', 'google-secret')):
            patcher = mock.patch.object(oauth2_settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(oauth2, 'http_json', FakeProvider({
            oauth2.GoogleAuthConsumer.token_url: {'access_token': 'token-1'},
            oauth2.GoogleAuthConsumer.userinfo_url: GOOGLE_PROFILE}))
        patcher.start()
        self.addCleanup(patcher.stop)

        user = self.create_user('carol')
        AuthKeyUserAssociation.objects.create(user=user, key='google:1234567890', provider='google')
        raw, _codes = self.enable_totp(user)
        start = self.client.get('/account/google/signin/')
        state = parse_qs(urlparse(start['Location']).query)['state'][0]
        response = self.client.get('/account/google/done/', {'code': 'auth-code', 'state': state})
        self.assertRedirects(response, '/account/two-step/', fetch_redirect_response=False)
        self.assertIsNone(self.logged_in_user_id())
        self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertEqual(self.logged_in_user_id(), str(user.id))

    def test_members_without_two_step_sign_in_directly(self):
        user = self.create_user('carol', password='right-pass')
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.assertEqual(self.logged_in_user_id(), str(user.id))

    def test_wrong_codes_are_throttled(self):
        self.set_setting(settings.LOGIN_FAILURES_LIMIT, 3)
        user = self.create_user('carol', password='right-pass')
        raw, _codes = self.enable_totp(user)
        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        with self.assertLogs('forum.security', 'WARNING') as logs:
            for _i in range(3):
                self.client.post('/account/two-step/', {'code': '99999x'})
        self.assertTrue(any('two_factor_failed' in line for line in logs.output))
        self.assertFalse(any(raw.hex() in line or '99999x' in line for line in logs.output))
        page = self.client.post('/account/two-step/', {'code': two_factor.totp(raw)})
        self.assertContains(page, 'Too many wrong codes')
        self.assertIsNone(self.logged_in_user_id())


class StaffRequirementTest(TwoFactorTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.moderator = self.create_user('mod', is_staff=True)

    def test_off_by_default(self):
        self.login(self.admin)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_staff_without_two_step_are_sent_to_set_it_up(self):
        self.set_setting(settings.TWO_FACTOR_REQUIRED_FOR_STAFF, True)
        self.login(self.admin)
        self.assertRedirects(self.client.get('/admin/'), '/account/%d/security/' % self.admin.id,
                             fetch_redirect_response=False)
        page = self.client.get('/account/%d/security/' % self.admin.id)
        self.assertContains(page, 'required for moderators and administrators')

        self.login(self.moderator)
        self.assertRedirects(self.client.get('/admin/moderation/'), '/account/%d/security/' % self.moderator.id,
                             fetch_redirect_response=False)

        self.enable_totp(self.admin)
        self.login(self.admin)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_passkey_sign_in_counts(self):
        self.set_setting(settings.TWO_FACTOR_REQUIRED_FOR_STAFF, True)
        self.login(self.admin)
        session = self.client.session
        session[two_factor.METHOD_SESSION_KEY] = 'passkey'
        session.save()
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_members_are_not_affected(self):
        self.set_setting(settings.TWO_FACTOR_REQUIRED_FOR_STAFF, True)
        member = self.create_user('carol')
        self.login(member)
        self.assertEqual(self.client.get('/questions/').status_code, 200)


class PasskeyTest(TwoFactorTestCase):
    def register(self, user, authenticator, name='My phone'):
        self.login(user)
        options = self.post_json('/account/passkeys/register/options/').json()
        self.assertEqual(options['rp']['id'], 'testserver')
        self.assertEqual(options['authenticatorSelection']['residentKey'], 'required')
        return self.post_json('/account/passkeys/register/', {'credential': authenticator.create(options),
                                                               'name': name})

    def sign_in(self, authenticator, count=True):
        options = self.post_json('/account/passkey-signin/options/').json()
        self.assertEqual(options['allowCredentials'], [])
        return self.post_json('/account/passkey-signin/', {'credential': authenticator.get(options, count)})

    def test_register_then_sign_in(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        response = self.register(user, authenticator)
        self.assertEqual(response.status_code, 200, response.content)
        passkey = Passkey.objects.get(user=user)
        self.assertEqual(passkey.name, 'My phone')
        self.assertEqual(passkey.credential_id, authenticator.id)
        self.assertIsNone(passkey.last_used)
        self.assertContains(self.client.get('/account/%d/security/' % user.id), 'My phone')

        self.client.logout()
        response = self.sign_in(authenticator)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('redirect', response.json())
        self.assertEqual(self.logged_in_user_id(), str(user.id))
        self.assertEqual(self.client.session[two_factor.METHOD_SESSION_KEY], 'passkey')
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 1)
        self.assertIsNotNone(passkey.last_used)

    def test_passkey_counts_as_two_factor(self):
        user = self.create_user('carol')
        self.enable_totp(user)
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        self.client.logout()
        self.sign_in(authenticator)
        self.assertEqual(self.logged_in_user_id(), str(user.id))

    def test_challenge_is_single_use_and_expires(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        self.client.logout()

        options = self.post_json('/account/passkey-signin/options/').json()
        answer = authenticator.get(options)
        self.assertEqual(self.post_json('/account/passkey-signin/', {'credential': answer}).status_code, 200)
        self.client.logout()
        self.assertEqual(self.post_json('/account/passkey-signin/', {'credential': answer}).status_code, 400)
        self.assertIsNone(self.logged_in_user_id())

        options = self.post_json('/account/passkey-signin/options/').json()
        with mock.patch.object(passkeys.time, 'time', return_value=time.time() + passkeys.CHALLENGE_MAX_AGE + 5):
            response = self.post_json('/account/passkey-signin/', {'credential': authenticator.get(options)})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.logged_in_user_id())

    def test_signature_counter_must_grow(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        self.client.logout()
        self.sign_in(authenticator)
        self.client.logout()
        with self.assertLogs('forum.security', 'WARNING') as logs:
            response = self.sign_in(authenticator, count=False)  # same counter again: a clone?
        self.assertEqual(response.status_code, 400)
        self.assertTrue(any('passkey_sign_count_error' in line for line in logs.output))
        self.assertIsNone(self.logged_in_user_id())

    def test_wrong_origin_and_unknown_passkeys_are_refused(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        self.client.logout()
        authenticator.origin = 'https://evil.example'
        self.assertEqual(self.sign_in(authenticator).status_code, 400)
        response = self.sign_in(SoftAuthenticator())
        self.assertEqual(response.status_code, 400)
        self.assertIn('not registered', response.json()['error'])
        self.assertIsNone(self.logged_in_user_id())

    def test_rp_id_comes_from_app_url(self):
        with mock.patch.object(settings, 'APP_URL', 'https://forum.example.org:8443/qa'):
            self.assertEqual(passkeys.rp_id(), 'forum.example.org')
            self.assertEqual(passkeys.expected_origin(), 'https://forum.example.org:8443')

    def test_same_passkey_twice_and_registration_needs_sign_in(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        options = self.post_json('/account/passkeys/register/options/').json()
        self.assertEqual([c['id'] for c in options['excludeCredentials']], [authenticator.id])
        response = self.post_json('/account/passkeys/register/', {'credential': authenticator.create(options)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Passkey.objects.count(), 1)

        self.client.logout()
        self.assertEqual(self.post_json('/account/passkeys/register/options/').status_code, 403)
        self.assertEqual(self.client.get('/account/passkey-signin/options/').status_code, 405)

    def test_remove(self):
        carol, dave = self.create_user('carol'), self.create_user('dave')
        self.register(carol, SoftAuthenticator())
        passkey = Passkey.objects.get(user=carol)
        self.login(dave)
        self.client.post('/account/passkeys/%d/remove/' % passkey.id)
        self.assertTrue(Passkey.objects.filter(id=passkey.id).exists())
        self.login(carol)
        self.client.post('/account/passkeys/%d/remove/' % passkey.id)
        self.assertFalse(Passkey.objects.filter(id=passkey.id).exists())

    def test_sign_in_page_offers_passkeys(self):
        page = self.client.get('/account/signin/')
        self.assertContains(page, 'data-passkey-signin')
        self.assertContains(page, 'autocomplete="username webauthn"')
        self.assertContains(page, 'osqa.passkeys.js')

    def test_suspended_member_cannot_sign_in_with_a_passkey(self):
        user = self.create_user('carol')
        authenticator = SoftAuthenticator()
        self.register(user, authenticator)
        user = User.objects.get(id=user.id)
        user.is_active = False
        user.save()
        self.client.logout()
        # Sent to the home page with the suspension message, not signed in.
        self.sign_in(authenticator)
        self.assertIsNone(self.logged_in_user_id())
