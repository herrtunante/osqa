"""The sign-in, sign-up and "forgot your password" pages: how they look on a
desktop and on a phone in dark mode (screenshots saved in tmp/e2e/), the
show-password button, the errors of a bad sign-up (summary and next to each
field, with the values kept), the links between the pages, and the sign-in
services shown as labelled buttons once they are configured."""
import os

from forum.models import User

from .base import ARTIFACTS, E2ETestCase, PASSWORD
from .test_accessibility import FINISHED_ANIMATING

PAGES = (('signin', '/account/signin/'), ('signup', '/account/local/register/'), ('forgot', '/account/tempsignin/'))
VIEWS = (('desktop', False), ('mobile', True))


class SignInPagesTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner')  # the first account would become the site owner

    def enable_providers(self):
        from forum_modules.oauth2auth import settings as oauth_settings
        for name in ('GITHUB_CLIENT_ID', 'GITHUB_CLIENT_SECRET', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'):
            self.set_setting(getattr(oauth_settings, name), 'fake-%s' % name.lower())

    def screenshot(self, page, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.wait_for_selector('.password-toggle:not([hidden]), .auth-card:not(:has(.password-toggle))')
        page.wait_for_function(FINISHED_ANIMATING)  # the greeting message fades in
        page.screenshot(path=os.path.join(ARTIFACTS, 'signin-pages-%s.png' % name), full_page=True)

    def test_pages_on_desktop_and_phone(self):
        self.enable_providers()
        for device, dark in VIEWS:
            page = self.open(device=device, dark=dark)
            view = device + ('-dark' if dark else '')
            for name, path in PAGES:
                with self.subTest(page=name, view=view):
                    self.visit(page, path)
                    self.assertEqual(page.locator('h1').count(), 1)
                    self.screenshot(page, '%s-%s' % (name, view))
            # On a phone the whole sign-in form fits on the first screen:
            # the reasons to sign in come after it.
            self.visit(page, '/account/signin/')
            button = page.locator('#blogin').bounding_box()
            benefits = page.locator('.auth-benefits').bounding_box()
            self.assertLess(button['y'] + button['height'], page.viewport_size['height'])
            self.assertGreater(benefits['y'], button['y'])
            self.assertIn('Sign in to', page.locator('h1').inner_text())
            self.assertEqual(page.evaluate('document.activeElement.id'), 'id_username')

    def test_show_password_button(self):
        page = self.open()
        self.visit(page, '/account/signin/')
        toggle = page.get_by_role('button', name='Show password')
        toggle.wait_for()
        self.assertEqual(toggle.get_attribute('aria-controls'), 'id_password')
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')
        page.fill('#id_username', 'owner')
        page.fill('#id_password', PASSWORD)

        toggle.click()
        self.assertEqual(page.get_attribute('#id_password', 'type'), 'text')
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'true')
        self.assertEqual(page.input_value('#id_password'), PASSWORD)
        toggle.click()
        self.assertEqual(page.get_attribute('#id_password', 'type'), 'password')
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')

        # Shown while the form is sent: it is hidden again, and signing in works.
        toggle.click()
        with page.expect_navigation():
            page.click('#blogin')
        self.check_page(page)
        self.assertIn('owner', page.locator('#top').inner_text())

        # Both password fields of the sign-up form have one.
        page.context.clear_cookies()
        self.visit(page, '/account/local/register/')
        page.locator('#id_password2 + .password-toggle').click()
        self.assertEqual(page.get_attribute('#id_password2', 'type'), 'text')
        self.assertEqual(page.get_attribute('#id_password1', 'type'), 'password')

    def test_bad_sign_up_shows_the_errors_and_keeps_the_values(self):
        page = self.open(device='mobile', dark=True)
        self.visit(page, '/account/local/register/')
        # What the fields are for, and the password rules from AUTH_PASSWORD_VALIDATORS.
        self.assertIn('never shown publicly', page.locator('#id_email-help').inner_text())
        rules = page.locator('#id_password1-help li').all_inner_texts()
        self.assertIn('At least 8 characters', rules)
        self.assertIn('id_password1-help', page.get_attribute('#id_password1', 'aria-describedby'))
        self.assertEqual(page.get_attribute('#id_password1', 'autocomplete'), 'new-password')

        page.fill('#id_username', 'owner')  # taken
        page.fill('#id_email', 'not-an-address')
        page.fill('#id_password1', 'short')
        page.fill('#id_password2', 'short')
        with page.expect_navigation():
            page.click('#bnewaccount')
        self.check_page(page)
        self.assertFalse(User.objects.filter(email='not-an-address').exists())

        summary = page.locator('#form-errors[role=alert]')
        self.assertEqual(page.evaluate('document.activeElement.id'), 'form-errors')
        text = summary.inner_text()
        for expected in ('That username is taken', 'name@example.com', 'too short'):
            self.assertIn(expected, text)
        self.assertEqual(summary.locator('a[href="#id_email"]').count(), 1)
        self.assertIn('taken', page.locator('#id_username-error').inner_text())
        self.assertIn('name@example.com', page.locator('#id_email-error').inner_text())
        self.assertIn('too short', page.locator('#id_password1-error').inner_text())
        self.assertEqual(page.get_attribute('#id_email', 'aria-invalid'), 'true')
        self.assertIn('id_email-error', page.get_attribute('#id_email', 'aria-describedby'))
        self.assertEqual(page.input_value('#id_username'), 'owner')
        self.assertEqual(page.input_value('#id_email'), 'not-an-address')
        self.screenshot(page, 'signup-errors-mobile-dark')

        page.fill('#id_username', 'newcomer')
        page.fill('#id_email', 'newcomer@example.com')
        page.fill('#id_password1', 'green-tree-river-7')
        page.fill('#id_password2', 'green-tree-river-7')
        with page.expect_navigation():
            page.click('#bnewaccount')
        self.check_page(page)
        self.assertTrue(User.objects.filter(username='newcomer', email='newcomer@example.com').exists())
        self.assertIn('newcomer', page.locator('#top').inner_text())

    def test_links_between_the_pages(self):
        page = self.open()
        self.visit(page, '/account/signin/')
        with page.expect_navigation():
            page.get_by_role('link', name='Create an account').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/account/local/register/'))
        self.assertEqual(page.locator('h1').inner_text(), 'Create your account')

        with page.expect_navigation():
            page.locator('.auth-switch').get_by_role('link', name='Sign in').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/account/signin/'))

        with page.expect_navigation():
            page.get_by_role('link', name='Forgot your password?').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/account/tempsignin/'))
        self.assertEqual(page.locator('h1').inner_text(), 'Forgot your password?')
        self.assertEqual(page.evaluate('document.activeElement.id'), 'id_email')

        with page.expect_navigation():
            page.get_by_role('link', name='Back to sign in').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/account/signin/'))

    def test_sign_in_with_email_address(self):
        page = self.open()
        self.visit(page, '/account/signin/')
        page.fill('#id_username', 'owner@example.com')
        page.fill('#id_password', PASSWORD)
        with page.expect_navigation():
            page.click('#blogin')
        self.check_page(page)
        self.assertIn('owner', page.locator('#top').inner_text())

    def test_wrong_password_keeps_the_username(self):
        page = self.open()
        self.visit(page, '/account/signin/')
        page.fill('#id_username', 'owner')
        page.fill('#id_password', 'not-the-password')
        with page.expect_navigation():
            page.click('#blogin')
        self.check_page(page)
        self.assertIn("don't match", page.locator('.auth-alert[role=alert]').inner_text())
        self.assertEqual(page.input_value('#id_username'), 'owner')
        self.assertEqual(page.get_attribute('#id_password', 'aria-describedby'), 'signin-error')

    def test_providers_are_labelled_buttons_when_enabled(self):
        page = self.open()
        self.visit(page, '/account/signin/')
        self.assertEqual(page.locator('.auth-providers, .auth-divider').count(), 0)

        self.enable_providers()
        for path in ('/account/signin/', '/account/local/register/'):
            with self.subTest(path=path):
                self.visit(page, path)
                buttons = page.locator('.auth-providers a')
                self.assertEqual(buttons.all_inner_texts(), ['Continue with Google', 'Continue with GitHub'])
                self.assertEqual(page.locator('.auth-providers a svg').count(), 2)
                self.assertEqual(page.get_by_role('link', name='Continue with Facebook').count(), 0)
                self.assertEqual(page.locator('.auth-divider').inner_text().strip().lower(), 'or')
                # Full width: as wide as the fields below.
                provider = page.get_by_role('link', name='Continue with GitHub').bounding_box()
                field = page.locator('#id_username').bounding_box()
                self.assertAlmostEqual(provider['width'], field['width'], delta=2)
                self.assertGreaterEqual(provider['height'], 44)
