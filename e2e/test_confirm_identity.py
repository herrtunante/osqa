""""Confirm it's you" (forum/utils/sudo.py) in the browser: a session whose
sign-in is older than REAUTH_MINUTES is sent to /account/confirm/ before a
sensitive change, confirms with the password, a passkey (Chromium's virtual
authenticator) or an emailed link, and comes back to send the form again."""
import os
import re
from unittest import mock

from django.conf import settings as djsettings
from django.contrib.sessions.backends.db import SessionStore

from forum import settings
from forum.models import ApiToken

from .base import ARTIFACTS, PASSWORD, E2ETestCase


class ConfirmIdentityTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner')  # the first account would become the site owner
        self._patch(mock.patch.object(settings, 'APP_URL', self.live_server_url))

    def screenshot(self, page, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'confirm-identity-%s.png' % name), full_page=True)

    def create_token(self, page, name):
        page.fill('#id_name', name)
        with page.expect_navigation():
            page.click('button:has-text("Create token")')

    def test_password_on_desktop_and_on_a_phone_in_dark_mode(self):
        for n, (device, dark) in enumerate((('desktop', False), ('mobile', True))):
            carol = self.create_user('carol%d' % n)
            tokens = '/account/%d/api-tokens/' % carol.id
            page = self.open(carol, device=device, dark=dark, recent=False)
            self.visit(page, tokens)
            self.create_token(page, 'Report script')
            self.assertIn('/account/confirm/', page.url)
            self.assertEqual(page.locator('h1').inner_text(), "Confirm it's you")
            self.assertFalse(ApiToken.objects.filter(user=carol).exists())
            self.check_page(page, device)
            self.screenshot(page, '%s%s' % (device, '-dark' if dark else ''))

            page.fill('#id_confirm_password', 'not-the-password')
            with page.expect_navigation():
                page.click('button:has-text("Confirm")')
            self.assertIn('That password is not right', page.locator('.auth-alert').inner_text())
            self.assertEqual(page.evaluate('() => document.activeElement.id'), 'id_confirm_password')
            page.fill('#id_confirm_password', PASSWORD)
            with page.expect_navigation():
                page.click('button:has-text("Confirm")')
            self.assertTrue(page.url.endswith(tokens), page.url)
            self.create_token(page, 'Report script')  # sent again: now it works
            self.assertTrue(ApiToken.objects.filter(user=carol, name='Report script').exists())
            self.assertEqual(len([m for m in self.mail_to(carol) if 'Security change' in m[1]]), 1)
            self.check_page(page, device)

    def test_passkey(self):
        carol = self.create_user('carol')
        page = self.open(carol)
        cdp = page.context.new_cdp_session(page)
        cdp.send('WebAuthn.enable')
        cdp.send('WebAuthn.addVirtualAuthenticator', {'options': {
            'protocol': 'ctap2', 'transport': 'internal', 'hasResidentKey': True, 'hasUserVerification': True,
            'isUserVerified': True, 'automaticPresenceSimulation': True}})
        self.visit(page, '/account/%d/security/' % carol.id)
        page.locator('form[data-passkey-register]').wait_for(state='visible')
        page.fill('#passkey-name', 'Laptop')
        with page.expect_navigation():
            page.click('button:has-text("Add a passkey")')

        # Time passes: the sign-in is no longer recent.
        key = [c for c in page.context.cookies() if c['name'] == djsettings.SESSION_COOKIE_NAME][0]['value']
        session = SessionStore(session_key=key)
        session.pop('osqa_auth_time', None)
        session.save()

        self.visit(page, '/account/security/two-step/')
        self.assertIn('/account/confirm/', page.url)
        button = page.locator('[data-passkey-button]')
        button.wait_for(state='visible')
        with page.expect_navigation():
            button.click()
        self.assertTrue(page.url.endswith('/account/security/two-step/'), page.url)
        page.locator('.twofa-qr-code').wait_for()
        self.check_page(page)

    def test_adding_a_passkey_after_a_while_goes_to_confirm_first(self):
        carol = self.create_user('carol')
        page = self.open(carol, recent=False)
        self.visit(page, '/account/%d/security/' % carol.id)
        page.locator('form[data-passkey-register]').wait_for(state='visible')
        with page.expect_navigation():
            page.click('button:has-text("Add a passkey")')
        self.assertIn('/account/confirm/?next=', page.url)
        self.check_page(page)

    def test_emailed_link_without_password_or_two_step(self):
        carol = self.create_user('carol')
        carol.set_unusable_password()
        carol.save()
        tokens = '/account/%d/api-tokens/' % carol.id
        page = self.open(carol, device='mobile', dark=True, recent=False)
        self.visit(page, '/account/confirm/?next=' + tokens)
        with page.expect_navigation():
            page.click('button:has-text("Email me a link")')
        self.assertIn('We emailed you a link', page.locator('.auth-lead').inner_text())
        self.check_page(page, 'mobile')
        mail = [m for m in self.mail_to(carol) if 'Confirm it' in m[1]][-1]
        link = re.search(r'(/account/confirm/\d+/[^/\s]+/)', mail[3]).group(1)
        self.visit(page, link)
        self.assertTrue(page.url.endswith(tokens), page.url)
        self.create_token(page, 'Script')
        self.assertTrue(ApiToken.objects.filter(user=carol).exists())
