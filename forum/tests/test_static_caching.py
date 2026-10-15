"""HTTP caching of skin media, Django's admin media and uploaded files."""
import gzip
import os
import shutil
import tempfile
from unittest import mock

from django.test import override_settings

from forum import settings
from forum.templatetags.extra_tags import media
from forum.utils import static_files
from forum.views import pwa

from .base import OSQATestCase

LONG = 'public, max-age=31536000, immutable'


class MediaVersionTest(OSQATestCase):

    def test_media_urls_carry_the_file_hash(self):
        url = media('/media/style/modern.css')
        path = os.path.join(pwa.SKINS_ROOT, 'default', 'media', 'style', 'modern.css')
        self.assertEqual(url, '/m/default/media/style/modern.css?v=%s' % static_files.file_token(path))
        self.assertRegex(url, r'\?v=[0-9a-f]{12}$')

    def test_pages_link_versioned_media(self):
        html = self.client.get('/').content.decode()
        self.assertRegex(html, r'/m/default/media/vendor/jquery-3\.7\.1\.min\.js\?v=[0-9a-f]{12}"')
        self.assertRegex(html, r'/m/default/media/style/modern\.css\?v=[0-9a-f]{12}"')

    def test_token_follows_content(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, 'a.js')
        with open(path, 'w') as f:
            f.write('one')
        first = static_files.file_token(path)
        with override_settings(DEBUG=True):
            with open(path, 'w') as f:
                f.write('two!')
            second = static_files.file_token(path)
        self.assertNotEqual(first, second)
        self.assertIsNone(static_files.file_token(os.path.join(folder, 'missing.js')))

    def test_service_worker_version_uses_the_same_hashes(self):
        with mock.patch.object(pwa, '_version', None):
            before = pwa.version()
        with mock.patch.object(pwa, '_version', None), \
                mock.patch.object(static_files, 'file_token', lambda path: 'changed'):
            after = pwa.version()
        self.assertNotEqual(before, after)


class MediaHeadersTest(OSQATestCase):

    def get(self, url, **headers):
        return self.client.get(url, **headers)

    def test_versioned_request_is_immutable(self):
        url = media('/media/js/osqa.main.js')
        response = self.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], LONG)
        self.assertTrue(response['ETag'].startswith('"'))
        self.assertIn('Last-Modified', response)
        self.assertIn('javascript', response['Content-Type'])
        self.assertNotIn('Set-Cookie', response)
        self.assertFalse(response.cookies)
        self.assertNotIn('Cookie', response.get('Vary', ''))

    def test_unversioned_request_short_cache_and_revalidation(self):
        url = '/m/default/media/js/osqa.main.js'
        response = self.get(url)
        self.assertEqual(response['Cache-Control'], 'public, max-age=%d' % static_files.SHORT_MAX_AGE)

        not_modified = self.get(url, HTTP_IF_NONE_MATCH=response['ETag'])
        self.assertEqual(not_modified.status_code, 304)
        self.assertEqual(not_modified['ETag'], response['ETag'])
        self.assertEqual(not_modified.content, b'')

        not_modified = self.get(url, HTTP_IF_MODIFIED_SINCE=response['Last-Modified'])
        self.assertEqual(not_modified.status_code, 304)

        changed = self.get(url, HTTP_IF_NONE_MATCH='"0123456789ab"')
        self.assertEqual(changed.status_code, 200)

    def test_old_token_is_not_cached_as_immutable(self):
        response = self.get('/m/default/media/js/osqa.main.js?v=000000000000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_gzip_for_text_assets(self):
        url = media('/media/style/modern.css')
        plain = b''.join(self.get(url).streaming_content)
        response = self.get(url, HTTP_ACCEPT_ENCODING='gzip, deflate, br')
        self.assertEqual(response['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', response['Vary'])
        self.assertEqual(gzip.decompress(response.content), plain)
        self.assertEqual(int(response['Content-Length']), len(response.content))
        self.assertTrue(response['ETag'].endswith('-gz"'))
        # Either representation's tag validates.
        self.assertEqual(self.get(url, HTTP_ACCEPT_ENCODING='gzip', HTTP_IF_NONE_MATCH=response['ETag'])
                         .status_code, 304)

    def test_images_and_pages_are_not_gzipped(self):
        response = self.get(media('/media/images/nophoto.png'), HTTP_ACCEPT_ENCODING='gzip')
        self.assertNotIn('Content-Encoding', response)
        self.assertEqual(response['Cache-Control'], LONG)
        page = self.client.get('/', HTTP_ACCEPT_ENCODING='gzip')
        self.assertNotIn('Content-Encoding', page)

    def test_missing_and_bad_paths(self):
        self.assertEqual(self.get('/m/default/media/js/nothing-here.js').status_code, 404)
        self.assertEqual(self.get('/m/django_admin/../../__init__.py').status_code, 400)

    def test_django_admin_media(self):
        response = self.get('/m/django_admin/css/base.css')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'public, max-age=%d' % static_files.SHORT_MAX_AGE)
        self.assertEqual(self.get('/m/django_admin/css/base.css', HTTP_IF_NONE_MATCH=response['ETag']).status_code,
                         304)


class UploadedFileHeadersTest(OSQATestCase):

    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        with open(os.path.join(self.folder, 'photo_x1y2.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'0' * 2000)
        settings.UPFILES_FOLDER.set_value(self.folder)
        self.addCleanup(settings.UPFILES_FOLDER.to_default)

    def test_uploads_are_cached_for_a_year(self):
        response = self.client.get('/upfiles/photo_x1y2.png')
        # Close the streamed file, or Windows can't delete the temporary folder.
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], LONG)
        self.assertEqual(self.client.get('/upfiles/photo_x1y2.png', HTTP_IF_NONE_MATCH=response['ETag']).status_code,
                         304)

    def test_shipped_logo_may_change_on_upgrade(self):
        response = self.client.get('/upfiles/logo.png')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'public, max-age=%d' % static_files.SHORT_MAX_AGE)

    def test_missing_upload(self):
        self.assertEqual(self.client.get('/upfiles/nothing.png').status_code, 404)
        self.assertEqual(self.client.get('/upfiles/../settings.py').status_code, 400)
