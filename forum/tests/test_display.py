"""Display preferences (forum/utils/display.py): the theme switch's view
(set_theme), how the choice reaches <html> and <head>, and the keyboard
shortcuts switch on the preferences page."""
import json

from django.test import RequestFactory
from django.urls import reverse

from forum import settings
from forum.utils import conditional, display

from .base import OSQATestCase


class ThemeViewTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('set_theme')

    def set_dark_mode(self, value):
        settings.DARK_MODE.set_value(value)
        self.addCleanup(setattr, settings.DARK_MODE, '_temp', None)

    def test_default_is_system(self):
        html = self.client.get('/').content.decode()
        self.assertIn('data-theme="light" data-theme-choice="auto" data-color-scheme="auto"', html)
        self.assertIn('<meta name="color-scheme" content="light dark" />', html)
        self.assertIn('media="(prefers-color-scheme: dark)"', html)
        self.assertIn('window.osqaTheme', html)
        self.assertIn('<select id="theme-select" name="theme">', html)
        self.assertIn('<option value="auto" selected>System</option>', html)
        self.assertIn('osqa.theme.js', html)

    def test_anonymous_form_saves_a_cookie_and_returns(self):
        response = self.client.post(self.url, {'theme': 'dark', 'next': '/questions/'})
        self.assertRedirects(response, '/questions/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[display.THEME_COOKIE].value, 'dark')
        self.assertEqual(response.cookies[display.THEME_COOKIE]['samesite'], 'Lax')

        html = self.client.get('/').content.decode()
        self.assertIn('data-theme="dark" data-theme-choice="dark"', html)
        self.assertNotIn('data-color-scheme="auto"', html)
        self.assertIn('<meta name="color-scheme" content="dark" />', html)
        self.assertIn('<meta name="theme-color" content="#161b22" media="all" />', html)
        self.assertIn('<meta name="theme-color" content="#2c5aa0" media="not all" />', html)
        self.assertIn('<option value="dark" selected>Dark</option>', html)

        self.client.post(self.url, {'theme': 'light'})
        html = self.client.get('/').content.decode()
        self.assertIn('data-theme="light" data-theme-choice="light"', html)
        self.assertIn('<meta name="color-scheme" content="light" />', html)

    def test_script_gets_json(self):
        response = self.client.post(self.url, {'theme': 'light'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'theme': 'light'})
        self.assertEqual(response.cookies[display.THEME_COOKIE].value, 'light')

    def test_refusals(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, {'theme': 'purple'}).status_code, 400)
        self.assertEqual(self.client.post(self.url).status_code, 400)
        # Another site as the way back: home instead.
        response = self.client.post(self.url, {'theme': 'dark', 'next': 'https://evil.example/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_member_choice_is_kept_in_preferences(self):
        user = self.create_user('alice')
        user.prop.preferences = {'sticky_sorts': True}
        self.login(user)
        self.client.post(self.url, {'theme': 'dark'}, HTTP_ACCEPT='application/json')
        user = type(user).objects.get(id=user.id)
        self.assertEqual(user.prop.preferences, {'sticky_sorts': True, 'theme': 'dark'})

        # The member's choice wins over this browser's cookie (set on another device).
        self.client.cookies[display.THEME_COOKIE] = 'light'
        html = self.client.get('/').content.decode()
        self.assertIn('data-theme="dark" data-theme-choice="dark"', html)
        self.assertIn('class="theme-menu"', html)
        self.assertIn('value="dark" checked', html)
        # Members' pages don't let the cookie override them in the head script.
        self.assertIn('document.documentElement, false);', html)

    def test_anonymous_head_script_reads_the_cookie(self):
        self.assertIn('document.documentElement, true);', self.client.get('/').content.decode())

    def test_dark_mode_off(self):
        self.set_dark_mode(False)
        self.client.cookies[display.THEME_COOKIE] = 'dark'
        html = self.client.get('/').content.decode()
        self.assertNotIn('data-theme', html)
        self.assertNotIn('theme-select', html)
        self.assertNotIn('osqa.theme.js', html)
        self.assertNotIn('osqaTheme', html)
        self.assertEqual(self.client.post(self.url, {'theme': 'dark'}).status_code, 400)

    def test_admin_follows_the_choice(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        admin.prop.preferences = {'theme': 'dark'}
        self.login(admin)
        html = self.client.get(reverse('admin_index')).content.decode()
        self.assertIn('data-theme="dark" data-theme-choice="dark"', html)
        self.assertIn('window.osqaTheme', html)

    def test_anonymous_etag_depends_on_the_theme(self):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.messages.storage.fallback import FallbackStorage

        def etag(cookie):
            request = RequestFactory().get('/questions/')
            if cookie:
                request.COOKIES[display.THEME_COOKIE] = cookie
            request.user = AnonymousUser()
            request.session = SessionStore()
            request._messages = FallbackStorage(request)
            return conditional.anonymous_page_etag(request)

        self.assertIsNotNone(etag(None))
        self.assertEqual(etag(None), etag('auto'))
        self.assertNotEqual(etag(None), etag('dark'))


class ShortcutsPreferenceTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user('bob')
        self.login(self.user)
        self.url = reverse('user_preferences', kwargs={'id': self.user.id, 'slug': 'bob'})

    def test_on_by_default(self):
        html = self.client.get('/').content.decode()
        self.assertNotIn('data-shortcuts="off"', html)
        self.assertIn('id="shortcuts-dialog"', html)
        self.assertIn('osqa.shortcuts.js', html)
        self.assertIn('data-url-notifications="/notifications/"', html)
        self.assertIn(self.url, html)  # "turn them off in your preferences"
        self.assertIn('name="keyboard_shortcuts" id="id_keyboard_shortcuts" checked', self.client.get(self.url).content.decode())

    def test_turning_off_keeps_the_other_preferences(self):
        self.user.prop.preferences = {'theme': 'dark', 'sticky_sorts': False}
        response = self.client.post(self.url, {'sticky_sorts': 'on', 'save': 'Save'})
        self.assertEqual(response.status_code, 200)
        # This page already reflects the change.
        self.assertContains(response, 'data-shortcuts="off"')
        user = type(self.user).objects.get(id=self.user.id)
        self.assertEqual(user.prop.preferences, {'theme': 'dark', 'sticky_sorts': True, 'keyboard_shortcuts': False})

        html = self.client.get('/').content.decode()
        self.assertIn('data-shortcuts="off"', html)
        self.assertNotIn('shortcuts-dialog', html)
        self.assertNotIn('osqa.shortcuts.js', html)
        self.assertIn('data-theme="dark"', html)

        self.client.post(self.url, {'keyboard_shortcuts': 'on', 'save': 'Save'})
        self.assertIn('id="shortcuts-dialog"', self.client.get('/').content.decode())

    def test_anonymous_visitors_get_them(self):
        self.client.logout()
        html = self.client.get('/').content.decode()
        self.assertIn('id="shortcuts-dialog"', html)
        self.assertNotIn('data-url-notifications', html)
        self.assertIn('Members can turn them off in their preferences.', html)
