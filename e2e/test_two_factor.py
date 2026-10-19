"""Passkeys and two-step verification in the browser.

Passkeys use Chromium's virtual authenticator (DevTools protocol
WebAuthn.addVirtualAuthenticator: a platform authenticator with resident keys
and user verification that answers by itself). The authenticator app is the
test: it reads the key shown on the setup page and computes the codes.
"""
import base64
import os
import time
from unittest import mock

from forum import settings
from forum.models import Passkey, TOTPDevice
from forum.utils import two_factor

from .base import ARTIFACTS, E2ETestCase, PASSWORD


def code_for(secret_text, offset=0):
    """What an authenticator app shows for the key on the setup page, `offset` seconds from now."""
    text = secret_text.replace(' ', '')
    raw = base64.b32decode(text + '=' * (-len(text) % 8))
    return two_factor.totp(raw, time.time() + offset)


class TwoFactorTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner')  # the first account would become the site owner
        # Passkeys are bound to APP_URL's host and origin: the live server's here.
        self._patch(mock.patch.object(settings, 'APP_URL', self.live_server_url))

    def virtual_authenticator(self, page):
        cdp = page.context.new_cdp_session(page)
        cdp.send('WebAuthn.enable')
        cdp.send('WebAuthn.addVirtualAuthenticator', {'options': {
            'protocol': 'ctap2', 'transport': 'internal', 'hasResidentKey': True, 'hasUserVerification': True,
            'isUserVerified': True, 'automaticPresenceSimulation': True}})
        return cdp

    def screenshot(self, page, name):
        """Saved in tmp/e2e/ for looking at the pages (desktop, phone, dark)."""
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'two-factor-%s.png' % name), full_page=True)

    def signed_in_as(self, page):
        """The id of the signed-in member ({% pwa_head %} gives it to osqa.pwa.js), '' when anonymous."""
        return page.locator('script[data-user]').get_attribute('data-user')

    def test_passkey_register_and_sign_in(self):
        carol = self.create_user('carol')
        page = self.open(carol)
        self.virtual_authenticator(page)

        self.visit(page, '/account/%d/security/' % carol.id)
        form = page.locator('form[data-passkey-register]')
        form.wait_for(state='visible')
        page.fill('#passkey-name', 'Work laptop')
        with page.expect_navigation():
            form.get_by_role('button', name='Add a passkey').click()
        self.check_page(page)
        self.assertIn('Work laptop', page.locator('.security-passkeys').inner_text())
        self.screenshot(page, 'security-passkey-desktop')
        self.assertIn('never used', page.locator('.security-passkeys').inner_text())
        self.assertEqual(Passkey.objects.filter(user=carol).count(), 1)

        # Signed out, the same browser signs in with the passkey alone: offered
        # in the username field's autofill (the virtual authenticator picks it
        # by itself)...
        page.context.clear_cookies()
        with page.expect_navigation(url=lambda url: '/account/signin/' not in url):
            page.goto(self.url('/account/signin/'))
        self.check_page(page)
        self.assertEqual(self.signed_in_as(page), str(carol.id))
        passkey = Passkey.objects.get(user=carol)
        self.assertIsNotNone(passkey.last_used)
        self.assertGreater(passkey.sign_count, 0)

        # ...and with the button, in browsers without that autofill.
        page.context.clear_cookies()
        page.add_init_script('PublicKeyCredential.isConditionalMediationAvailable = '
                             '() => Promise.resolve(false);')
        self.visit(page, '/account/signin/')
        self.assertEqual(self.signed_in_as(page), '')
        button = page.get_by_role('button', name='Sign in with a passkey')
        button.wait_for(state='visible')
        self.screenshot(page, 'signin-desktop')
        with page.expect_navigation():
            button.click()
        self.check_page(page)
        self.assertEqual(self.signed_in_as(page), str(carol.id))

        # Remove it again (the confirmation is accepted).
        self.visit(page, '/account/%d/security/' % carol.id)
        self.assertIn('last used', page.locator('.security-passkeys').inner_text())
        with page.expect_navigation():
            page.locator('.security-passkeys button.danger-outline-button').click()
        self.check_page(page)
        self.assertEqual(Passkey.objects.filter(user=carol).count(), 0)
        self.assertIn('No passkeys yet', page.locator('.security-card').first.inner_text())

    def test_sign_in_page_hides_passkeys_without_webauthn(self):
        page = self.open()
        page.add_init_script('delete window.PublicKeyCredential;')
        self.visit(page, '/account/signin/')
        self.assertTrue(page.locator('[data-passkey-signin]').is_hidden())

    def test_authenticator_app_setup_and_sign_in_on_a_phone(self):
        dave = self.create_user('dave')
        page = self.open(dave, device='mobile', dark=True)
        self.visit(page, '/account/%d/security/' % dave.id)
        self.assertIn('Off', page.locator('.security-status').inner_text())
        page.get_by_role('link', name='Set up an authenticator app').click()
        page.wait_for_url('**/account/security/two-step/')
        self.check_page(page)
        self.assertEqual(page.locator('.security-qr-image svg').count(), 1)
        secret = page.locator('#totp-secret').inner_text()
        self.screenshot(page, 'setup-mobile-dark')

        page.fill('#totp-code', '000000' if code_for(secret) != '000000' else '111111')
        page.get_by_role('button', name='Turn on two-step verification').click()
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertTrue(page.locator('#totp-setup-error').is_visible())

        page.fill('#totp-code', code_for(secret))
        page.get_by_role('button', name='Turn on two-step verification').click()
        page.wait_for_selector('#recovery-codes')
        self.check_page(page)
        codes = page.locator('#recovery-codes code').all_inner_texts()
        self.assertEqual(len(codes), 10)
        self.screenshot(page, 'recovery-codes-mobile-dark')
        page.get_by_role('link', name='I have saved them').click()
        page.wait_for_url('**/account/%d/security/' % dave.id)
        self.check_page(page)
        self.assertIn('On', page.locator('.security-status').inner_text())
        self.assertIn('10 codes left', page.locator('.security-card').nth(1).inner_text())
        page.locator('summary', has_text='Turn off two-step verification').click()
        self.screenshot(page, 'security-on-mobile-dark')

        # New recovery codes, confirmed with the password.
        page.locator('summary', has_text='Make new recovery codes').click()
        page.fill('#rc-password', PASSWORD)
        page.get_by_role('button', name='Make new codes').click()
        page.wait_for_selector('#recovery-codes')
        self.check_page(page)
        new_codes = page.locator('#recovery-codes code').all_inner_texts()
        self.assertEqual(len(new_codes), 10)
        self.assertFalse(set(codes) & set(new_codes))

        # Signing in now asks for a code after the password.
        page.context.clear_cookies()
        self.visit(page, '/account/signin/')
        page.fill('#id_username', 'dave')
        page.fill('#id_password', PASSWORD)
        page.click('#blogin')
        page.wait_for_url('**/account/two-step/')
        self.check_page(page)
        self.assertEqual(self.signed_in_as(page), '')
        self.assertEqual(page.locator('#id_code').get_attribute('autocomplete'), 'one-time-code')
        page.fill('#id_code', '12345x')
        page.get_by_role('button', name='Verify').click()
        page.wait_for_selector('#twofa-error')
        self.check_page(page)
        self.screenshot(page, 'verify-mobile-dark')
        # The next step's code (the setup used the current one, which can't be used twice).
        page.fill('#id_code', code_for(secret, 30))
        page.get_by_role('button', name='Verify').click()
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertEqual(self.signed_in_as(page), str(dave.id))

        # Turn it off again, with the password.
        self.visit(page, '/account/%d/security/' % dave.id)
        page.locator('summary', has_text='Turn off two-step verification').click()
        page.fill('#off-password', PASSWORD)
        page.get_by_role('button', name='Turn off', exact=True).click()
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertIn('Off', page.locator('.security-status').inner_text())
        self.assertFalse(TOTPDevice.objects.filter(user=dave).exists())

    def test_recovery_code_on_the_verification_page(self):
        erin = self.create_user('erin')
        raw = two_factor.new_secret()
        TOTPDevice.objects.create(user=erin, secret=two_factor.encrypt_secret(raw), confirmed_at='2026-01-01 00:00')
        codes = two_factor.new_recovery_codes(erin)
        page = self.open()
        self.visit(page, '/account/signin/')
        page.fill('#id_username', 'erin')
        page.fill('#id_password', PASSWORD)
        page.click('#blogin')
        page.wait_for_url('**/account/two-step/')
        page.locator('summary', has_text="Can't use your app?").click()
        page.fill('#id_code', codes[0])
        page.get_by_role('button', name='Verify').click()
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertEqual(self.signed_in_as(page), str(erin.id))
        self.assertIn('recovery code', page.locator('body').inner_text())

    def test_staff_are_sent_to_set_it_up(self):
        self.set_setting(settings.TWO_FACTOR_REQUIRED_FOR_STAFF, True)
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.open(admin)
        page.goto(self.url('/admin/'))
        page.wait_for_url('**/account/%d/security/' % admin.id)
        self.check_page(page)
        self.assertIn('required for moderators and administrators', page.locator('.settings-main').inner_text())
