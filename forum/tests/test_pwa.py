"""Installable app: the web app manifest, the service worker and the offline page."""
import json
import os
import shutil
import tempfile

from PIL import Image

from forum import settings
from forum.views import pwa

from .base import OSQATestCase


class PWATestCase(OSQATestCase):
    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)


class ManifestTest(PWATestCase):
    def manifest(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        self.assertIn('max-age=3600', response['Cache-Control'])
        return json.loads(response.content)

    def test_fields(self):
        self.set(settings.APP_TITLE, 'Collect Q&A forum')
        self.set(settings.APP_SHORT_NAME, 'Collect')
        self.set(settings.APP_DESCRIPTION, 'Questions about Collect.')
        data = self.manifest()
        self.assertEqual(data['name'], 'Collect Q&A forum')
        self.assertEqual(data['short_name'], 'Collect')
        self.assertEqual(data['description'], 'Questions about Collect.')
        self.assertEqual(data['start_url'], '/')
        self.assertEqual(data['scope'], '/')
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['theme_color'], '#2c5aa0')
        self.assertEqual(data['background_color'], '#f5f7fa')

    def test_bundled_icons_by_default(self):
        """The default logo is a wide word mark: the bundled square icons are used."""
        icons = self.manifest()['icons']
        self.assertEqual([(i['sizes'], i['purpose']) for i in icons],
                         [('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable')])
        for icon in icons:
            response = self.client.get(icon['src'])
            self.assertEqual(response.status_code, 200, icon['src'])
            with Image.open(os.path.join(pwa.SKINS_ROOT, icon['src'].split('/m/', 1)[1])) as image:
                self.assertEqual('%dx%d' % image.size, icon['sizes'])

    def test_square_uploaded_logo_is_the_icon(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        Image.new('RGB', (256, 256), 'red').save(os.path.join(folder, 'square.png'))
        Image.new('RGB', (600, 200), 'red').save(os.path.join(folder, 'wide.png'))
        Image.new('RGB', (64, 64), 'red').save(os.path.join(folder, 'tiny.png'))
        self.set(settings.UPFILES_FOLDER, folder)

        self.set(settings.APP_LOGO, '/upfiles/square.png')
        self.assertEqual(self.manifest()['icons'],
                         [{'src': '/upfiles/square.png', 'sizes': '256x256', 'type': 'image/png', 'purpose': 'any'}])
        self.assertIn('<link rel="apple-touch-icon" href="/upfiles/square.png" />',
                      self.client.get('/').content.decode())

        for unusable in ('/upfiles/wide.png', '/upfiles/tiny.png', '/upfiles/missing.png', '/upfiles/../settings.py',
                         'https://example.org/logo.png'):
            self.set(settings.APP_LOGO, unusable)
            self.assertEqual(len(self.manifest()['icons']), 3, unusable)


class ServiceWorkerTest(PWATestCase):
    def test_served_from_the_root_and_not_cached(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertIn('max-age=0', response['Cache-Control'])
        self.assertNotIn('Content-Security-Policy', response)
        text = response.content.decode()
        self.assertIn("const VERSION = '%s';" % pwa.version(), text)
        self.assertIn("const PREFIX = \"\";", text)
        self.assertIn('const PAGE_CACHE_SIZE = 50;', text)
        self.assertNotIn('{{', text)

    def test_version_follows_the_media_files(self):
        old = pwa.version()
        self.assertRegex(old, r'^[0-9a-f]{12}$')
        media = os.path.join(pwa.SKINS_ROOT, 'default', 'media', 'js', 'osqa.pwa.js')
        stat = os.stat(media)
        self.addCleanup(os.utime, media, (stat.st_atime, stat.st_mtime))
        self.addCleanup(setattr, pwa, '_version', None)
        os.utime(media, (stat.st_atime, stat.st_mtime + 10))
        pwa._version = None
        self.assertNotEqual(pwa.version(), old)

    def test_never_caches_personal_pages(self):
        text = self.client.get('/sw.js').content.decode()
        for part in ('admin', 'account', 'notifications', 'users', 'upfiles', 'mcp', 'export'):
            self.assertIn(part + '|', text.split('const NEVER = ', 1)[1].split('\n', 1)[0])
        self.assertIn("request.method !== 'GET'", text)
        self.assertIn('caches.delete(PAGES)', text)  # on sign-out


class PagesTest(PWATestCase):
    def test_head_links(self):
        html = self.client.get('/').content.decode()
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest" />', html)
        self.assertIn('<meta name="theme-color" content="#2c5aa0" media="(prefers-color-scheme: light)" />', html)
        self.assertIn('<meta name="theme-color" content="#161b22" media="(prefers-color-scheme: dark)" />', html)
        self.assertIn('<link rel="apple-touch-icon" href="/m/default/media/images/icons/apple-touch-icon.png" />', html)
        self.assertIn('/m/default/media/js/osqa.pwa.js" data-user=""', html)
        self.assertIn('id="offline-banner" class="offline-banner" role="status" hidden', html)

    def test_signed_in_user_is_given_to_the_script(self):
        alice = self.create_user('alice')
        self.login(alice)
        self.assertIn('osqa.pwa.js" data-user="%d"' % alice.pk, self.client.get('/').content.decode())

    def test_single_theme_colour_without_dark_mode(self):
        self.set(settings.DARK_MODE, False)
        html = self.client.get('/').content.decode()
        self.assertIn('<meta name="theme-color" content="#2c5aa0" />', html)
        self.assertNotIn('#161b22', html)

    def test_csp_allows_the_worker_and_manifest(self):
        policy = self.client.get('/')['Content-Security-Policy']
        self.assertIn("worker-src 'self'", policy)
        self.assertIn("manifest-src 'self'", policy)

    def test_offline_page(self):
        response = self.client.get('/offline/')
        self.assertCleanPage(response)
        html = response.content.decode()
        self.assertIn('You are offline', html)
        self.assertIn('id="offline-pages"', html)
        self.assertIn('<meta name="robots" content="noindex" />', html)


class MaintenanceTest(PWATestCase):
    def test_worker_and_manifest_stay_reachable(self):
        """Pages answer 503 (never a saved copy: the worker only falls back to
        its cache without a network), but the worker can still update."""
        self.set(settings.MAINTAINANCE_MODE, {'allow_ips': [], 'message': 'Back soon'})
        self.assertEqual(self.client.get('/').status_code, 503)
        self.assertEqual(self.client.get('/sw.js').status_code, 200)
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 200)
