"""Image variants (AVIF/WebP at several widths, forum/utils/images.py), the
<picture> elements post HTML gets for them, the backfill command, the
question page's priority image, the header logo size, and the Server-Timing
header (forum/middleware/server_timing.py)."""
import io
import json
import os
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.template import Context, Template
from django.test import override_settings
from PIL import Image, ImageDraw, features

from forum import settings
from forum.models import Question
from forum.models.node import render_markdown
from forum.utils import images
from forum.utils.html import priority_image

from .base import OSQATestCase

AVIF = features.check('avif')


def encode(img, fmt, **options):
    out = io.BytesIO()
    img.save(out, fmt, **options)
    return out.getvalue()


def picture(width, height):
    """An image with some detail (compresses like a photo, not like a flat colour)."""
    img = Image.new('RGB', (width, height), (30, 90, 160))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 7):
        draw.line((x, 0, width - x, height), fill=(x % 255, 200, 255 - x % 255), width=2)
    return img


class UploadsFolderMixin:
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        settings.UPFILES_FOLDER.set_value(self.folder)
        self.addCleanup(setattr, settings.UPFILES_FOLDER, '_temp', None)

    def path(self, name):
        return os.path.join(self.folder, name)

    def store(self, img, name, fmt='JPEG', **options):
        return images.store_upload(io.BytesIO(encode(img, fmt, **options)), name)

    def meta(self, name):
        with open(self.path('_variants/%s.json' % name)) as f:
            return json.load(f)


class VariantsTest(UploadsFolderMixin, OSQATestCase):
    def formats(self):
        return ['avif', 'webp'] if AVIF else ['webp']

    def test_upload_gets_modern_formats_at_smaller_widths(self):
        url = self.store(picture(1600, 1000), 'plot map.jpg')
        self.assertEqual(url, '/upfiles/plot_map.jpg')
        meta = self.meta('plot_map.jpg')
        self.assertEqual((meta['width'], meta['height']), (1600, 1000))
        self.assertEqual(sorted(meta['sources']), sorted(self.formats()))
        for ext in self.formats():
            self.assertEqual(meta['sources'][ext], [640, 1280, 1600])
            for width in (640, 1280, 1600):
                name = '_variants/plot_map.jpg.%dw.%s' % (width, ext)
                self.assertEqual(images.variant_name('plot_map.jpg', width, ext), name)
                with Image.open(self.path(name)) as img:
                    self.assertEqual(img.format, ext.upper())
                    self.assertEqual(img.size, (width, round(1000 * width / 1600)))
                    self.assertEqual(dict(img.getexif()), {})
                    self.assertNotIn('xmp', img.info)
        # The canonical file is the re-encoded upload, as before.
        with Image.open(self.path('plot_map.jpg')) as img:
            self.assertEqual((img.format, img.size), ('JPEG', (1600, 1000)))

    def test_avif_is_written_only_where_pillow_supports_it(self):
        with mock.patch.object(images.features, 'check', lambda name: name != 'avif'):
            self.store(picture(800, 600), 'shot.png', 'PNG')
        self.assertEqual(list(self.meta('shot.png')['sources']), ['webp'])
        self.assertFalse([n for n in os.listdir(self.path('_variants')) if n.endswith('.avif')])

    def test_small_image_gets_only_its_own_width(self):
        self.store(picture(500, 300), 'small.png', 'PNG')
        meta = self.meta('small.png')
        self.assertEqual((meta['width'], meta['height']), (500, 300))
        for ext in meta['sources']:
            self.assertEqual(meta['sources'][ext], [500])

    def test_copy_bigger_than_the_canonical_file_is_left_out(self):
        img = picture(1000, 500)
        with open(self.path('tiny.png'), 'wb') as f:
            f.write(encode(img, 'PNG'))
        os.makedirs(self.path('_variants'))
        meta = images._make_variants(Image.open(self.path('tiny.png')), self.folder, 'tiny.png', canonical_size=1)
        for ext in self.formats():
            self.assertEqual(meta['sources'][ext], [640])   # resized copies are kept
            self.assertFalse(os.path.exists(self.path('_variants/tiny.png.1000w.%s' % ext)))

    def test_animated_image_gets_only_its_size(self):
        frames = [Image.new('RGB', (120, 40), c) for c in ((255, 0, 0), (0, 255, 0))]
        url = self.store(frames[0], 'spinner.gif', 'GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
        self.assertEqual(url, '/upfiles/spinner.gif')
        self.assertEqual(self.meta('spinner.gif'), {'width': 120, 'height': 40, 'sources': {}})
        html = render_markdown('![spin](/upfiles/spinner.gif)')
        self.assertNotIn('<picture>', html)
        self.assertIn('<img loading="lazy" decoding="async" width="120" height="40" alt="spin" src="/upfiles/spinner.gif"', html)

    def test_old_upload_with_location_and_rotation(self):
        """Files uploaded before the re-encoding still carry EXIF: the copies don't."""
        photo = picture(300, 100)
        exif = Image.Exif()
        exif[0x0112] = 6   # rotate 90 degrees clockwise
        exif[0x8825] = {1: 'N', 2: (51.0, 30.0, 12.0)}
        with open(self.path('old.jpg'), 'wb') as f:
            f.write(encode(photo, 'JPEG', exif=exif.tobytes()))
        meta, written = images.write_variants(self.folder, 'old.jpg')
        self.assertTrue(written)
        self.assertEqual((meta['width'], meta['height']), (100, 300))
        with Image.open(self.path('_variants/old.jpg.100w.webp')) as img:
            self.assertEqual(img.size, (100, 300))
            self.assertEqual(dict(img.getexif()), {})
            self.assertNotIn('exif', img.info)

    def test_write_variants_is_idempotent(self):
        self.store(picture(700, 400), 'a.png', 'PNG')
        before = {n: os.path.getmtime(self.path('_variants/' + n)) for n in os.listdir(self.path('_variants'))}
        meta, written = images.write_variants(self.folder, 'a.png')
        self.assertFalse(written)
        self.assertEqual(meta, images.read_meta(self.folder, 'a.png'))
        after = {n: os.path.getmtime(self.path('_variants/' + n)) for n in os.listdir(self.path('_variants'))}
        self.assertEqual(before, after)
        # A missing copy is written again.
        os.remove(self.path(images.variant_name('a.png', 640, 'webp')))
        self.assertTrue(images.write_variants(self.folder, 'a.png')[1])
        self.assertTrue(os.path.exists(self.path(images.variant_name('a.png', 640, 'webp'))))

    def test_not_images_and_odd_names_are_skipped(self):
        with open(self.path('notes.txt'), 'w') as f:
            f.write('hello')
        self.assertEqual(images.write_variants(self.folder, 'notes.txt'), (None, False))
        for name in ('../x.png', '.hidden.png', '_variants/a.png.json', 'missing.png'):
            self.assertEqual(images.write_variants(self.folder, name), (None, False))
            self.assertIsNone(images.read_meta(self.folder, name))

    def test_upload_view_writes_variants_and_they_are_served(self):
        self.login(self.create_user('alice', reputation=500))
        response = self.client.post('/upload/', {'file-upload': SimpleUploadedFile('wide.png', encode(picture(900, 300),
                                                                                                    'PNG'))})
        self.assertEqual(response.json(), {'url': '/upfiles/wide.png'})
        for ext, media_type in (('webp', 'image/webp'), ('avif', 'image/avif')):
            if ext not in self.formats():
                continue
            served = self.client.get('/upfiles/_variants/wide.png.640w.%s' % ext)
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served['Content-Type'], media_type)
            self.assertEqual(served['Cache-Control'], 'public, max-age=31536000, immutable')
            self.assertNotIn('Set-Cookie', served)
            served.close()

    def test_variant_failure_keeps_the_upload(self):
        self.login(self.create_user('alice', reputation=500))
        with mock.patch.object(images, '_make_variants', side_effect=OSError('disk full')):
            response = self.client.post('/upload/', {'file-upload': SimpleUploadedFile('a.png', encode(picture(50, 50),
                                                                                                    'PNG'))})
        self.assertEqual(response.json(), {'url': '/upfiles/a.png'})
        self.assertTrue(os.path.exists(self.path('a.png')))
        self.assertIsNone(images.read_meta(self.folder, 'a.png'))


class PictureTest(UploadsFolderMixin, OSQATestCase):
    def setUp(self):
        super().setUp()
        self.store(picture(1600, 1000), 'map.jpg')

    def test_upload_becomes_picture_with_size(self):
        html = render_markdown('Text\n\n![The map](/upfiles/map.jpg)')
        self.assertIn('<picture>', html)
        webp = ('<source type="image/webp" srcset="/upfiles/_variants/map.jpg.640w.webp 640w, '
                '/upfiles/_variants/map.jpg.1280w.webp 1280w, /upfiles/_variants/map.jpg.1600w.webp 1600w" '
                'sizes="(max-width: 640px) 100vw, 760px">')
        self.assertIn(webp, html)
        if AVIF:
            self.assertLess(html.index('type="image/avif"'), html.index('type="image/webp"'))
        self.assertIn('<img loading="lazy" decoding="async" width="1600" height="1000" alt="The map" '
                      'src="/upfiles/map.jpg"></picture>', html)

    def test_absolute_url_of_this_site(self):
        html = render_markdown('<img src="http://testserver/upfiles/map.jpg" alt="m">')
        self.assertIn('<picture>', html)
        self.assertIn('width="1600" height="1000"', html)

    def test_external_and_unknown_images_are_left_alone(self):
        for markdown in ('![x](https://example.org/upfiles/map.jpg)', '![x](/upfiles/other.png)',
                         '![x](/upfiles/../upfiles/map.jpg)', '![x](/upfiles/_variants/map.jpg.640w.webp)'):
            with self.subTest(markdown=markdown):
                html = render_markdown(markdown)
                self.assertNotIn('<picture>', html)
                self.assertNotIn('width=', html)

    def test_size_given_by_the_author_is_kept(self):
        html = render_markdown('<img src="/upfiles/map.jpg" width="320" alt="m">')
        self.assertIn('width="320"', html)
        self.assertNotIn('height="1000"', html)
        self.assertIn('sizes="(max-width: 640px) 100vw, 320px"', html)

    def test_names_are_quoted_in_srcset(self):
        self.store(picture(700, 300), 'café, menu.png', 'PNG')
        html = render_markdown('![m](/upfiles/caf%C3%A9,_menu.png)')
        self.assertIn('/upfiles/_variants/caf%C3%A9%2C_menu.png.640w.webp 640w', html)

    def test_user_html_cannot_bring_picture_or_source(self):
        html = render_markdown('<picture><source srcset="https://evil.example/x.png" type="image/png">'
                               '<img src="https://evil.example/y.png" srcset="https://evil.example/z.png 2x" '
                               'loading="eager" fetchpriority="high"></picture>')
        self.assertNotIn('<picture', html)
        self.assertNotIn('<source', html)
        self.assertNotIn('srcset', html)
        self.assertNotIn('evil.example/x.png', html)
        self.assertNotIn('fetchpriority', html)
        self.assertEqual(html.count('loading='), 1)

    def test_question_page_first_image_has_priority(self):
        author = self.create_user('alice', reputation=500)
        question = Question(author=author, title='A question with pictures in its body',
                            body=render_markdown('Look:\n\n![a](/upfiles/map.jpg)\n\n![b](/upfiles/map.jpg)'),
                            tagnames='maps')
        question.save()
        page = self.client.get(question.get_absolute_url()).content.decode()
        body = page[page.index('question-body'):page.index('question-tags')]
        self.assertEqual(body.count('<img fetchpriority="high" decoding="async" width="1600"'), 1)
        self.assertEqual(body.count('<img loading="lazy" decoding="async" width="1600"'), 1)
        self.assertLess(body.index('fetchpriority'), body.index('loading="lazy"'))

    def test_priority_image_only_near_the_top(self):
        lazy = '<img loading="lazy" decoding="async" src="/a.png">'
        self.assertEqual(priority_image('<p>x</p>' + lazy + lazy),
                         '<p>x</p><img fetchpriority="high" decoding="async" src="/a.png">' + lazy)
        far = '<p>%s</p>%s' % ('x' * 3000, lazy)
        self.assertEqual(priority_image(far), far)
        self.assertEqual(priority_image('<p>no images</p>'), '<p>no images</p>')
        self.assertEqual(priority_image('<img src="/old.png">'), '<img fetchpriority="high" src="/old.png">')


class BackfillCommandTest(UploadsFolderMixin, OSQATestCase):
    def setUp(self):
        super().setUp()
        for name, size in (('one.png', (900, 500)), ('two.jpg', (300, 200))):
            with open(self.path(name), 'wb') as f:
                f.write(encode(picture(*size), 'JPEG' if name.endswith('.jpg') else 'PNG'))
        with open(self.path('README'), 'w') as f:
            f.write('not an image')

    def run_command(self, *args):
        out = io.StringIO()
        call_command('backfill_image_variants', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self.run_command('--dry-run')
        self.assertIn('Would write variants of 2 image(s), 0 already done, 1 other file(s) skipped.', output)
        self.assertFalse(os.path.exists(self.path('_variants')))

    def test_backfill_is_idempotent(self):
        self.assertIn('Wrote variants of 2 image(s), 0 already done, 1 other file(s) skipped.', self.run_command())
        self.assertEqual(images.read_meta(self.folder, 'one.png')['width'], 900)
        self.assertEqual(images.read_meta(self.folder, 'two.jpg')['height'], 200)
        files = sorted(os.listdir(self.path('_variants')))
        self.assertIn('Wrote variants of 0 image(s), 2 already done, 1 other file(s) skipped.', self.run_command())
        self.assertEqual(sorted(os.listdir(self.path('_variants'))), files)

    def test_rerender_turns_old_posts_into_pictures(self):
        author = self.create_user('alice', reputation=500)
        question = Question(author=author, title='An old question with an uploaded picture',
                            body=render_markdown('![a](/upfiles/one.png)'), tagnames='maps')
        question.save()
        self.assertNotIn('<picture>', Question.objects.get(id=question.id).body)
        self.run_command()
        output = self.run_command('--rerender', '--dry-run')
        self.assertIn('Would render again 1 post(s).', output)
        self.assertNotIn('<picture>', Question.objects.get(id=question.id).body)
        self.assertIn('Rendered again 1 post(s).', self.run_command('--rerender'))
        self.assertIn('<picture>', Question.objects.get(id=question.id).body)
        self.assertIn('Rendered again 0 post(s).', self.run_command('--rerender'))


class LogoAndAvatarTest(OSQATestCase):
    def test_logo_has_its_size(self):
        self.assertIn('width="180" height="74"', Template('{% load extra_tags %}{% logo_size %}').render(Context()))
        self.assertContains(self.client.get('/'), 'width="180" height="74"')

    def test_outside_logo_has_no_size(self):
        settings.APP_LOGO.set_value('https://example.org/logo.png')
        self.addCleanup(setattr, settings.APP_LOGO, '_temp', None)
        self.assertEqual(Template('{% load extra_tags %}{% logo_size %}').render(Context()), '')

    def test_gravatar_has_a_double_density_source(self):
        user = self.create_user('alice')
        html = Template('{% load extra_tags %}{% gravatar user 32 %}').render(Context({'user': user}))
        self.assertIn('width="32" height="32"', html)
        self.assertIn('?s=32&amp;', html)
        self.assertIn('?s=64&amp;d=', html)
        self.assertIn(' 2x"', html)
        self.assertIn('loading="lazy"', html)


class ServerTimingTest(OSQATestCase):
    def test_off_by_default(self):
        self.login(self.create_user('admin', is_staff=True, is_superuser=True))
        self.assertNotIn('Server-Timing', self.client.get('/'))

    @override_settings(SERVER_TIMING='staff')
    def test_staff_only_when_the_setting_is_on(self):
        self.assertNotIn('Server-Timing', self.client.get('/'))
        self.login(self.create_user('bob'))
        self.assertNotIn('Server-Timing', self.client.get('/'))
        self.login(self.create_user('admin', is_staff=True, is_superuser=True))
        header = self.client.get('/')['Server-Timing']
        self.assertRegex(header, r'^db;dur=[\d.]+;desc="\d+ queries", render;dur=[\d.]+, total;dur=[\d.]+$')
        queries = int(header.split('desc="')[1].split(' ')[0])
        self.assertGreater(queries, 0)
        render = float(header.split('render;dur=')[1].split(',')[0])
        self.assertGreater(render, 0)

    @override_settings(DEBUG=True)
    def test_everyone_with_debug(self):
        self.assertIn('Server-Timing', self.client.get('/questions/'))
