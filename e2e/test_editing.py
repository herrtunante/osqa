"""Editor details: form validation, image upload (and refused uploads),
editing answers and deleting comments."""
import os
import shutil
import struct
import tempfile
import zlib

from django.urls import reverse

from forum import settings
from forum.models import Answer, Comment

from .base import E2ETestCase


def tiny_png(path):
    """A valid 1x1 PNG (the upload checks the file's content, not only its name)."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
           chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


class EditingTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bob', reputation=500)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')

    def test_ask_form_validation(self):
        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        page.fill('#id_title', 'Help')
        page.fill('#editor', 'short')
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        content = page.content()
        self.assertIn('title must be at least', content)
        self.assertIn('Help', page.input_value('#id_title'))  # what was typed is kept

    def open_image_dialog(self, folder):
        self.set_setting(settings.UPFILES_FOLDER, folder)
        page = self.open(self.bob)
        self.visit(page, self.question.get_absolute_url())
        page.click('#wmd-image-button')
        page.wait_for_selector('#file-upload')
        return page

    def upload_folder(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        return folder

    def test_upload_an_image(self):
        folder = self.upload_folder()
        image = os.path.join(folder, 'plot-photo.png')
        tiny_png(image)

        page = self.open_image_dialog(folder)
        # The dialog's buttons and file input are disabled while the file is sent.
        page.evaluate("""() => {
            const send = window.fetch;
            window.fetch = function() {
                window.uploadControlsDisabled = Array.from(document.querySelectorAll(
                    '.wmd-prompt-dialog input[type=button], #file-upload')).every(e => e.disabled);
                return send.apply(this, arguments);
            };
        }""")
        with page.expect_response(lambda r: r.url.endswith('/upload/')) as upload:
            page.set_input_files('#file-upload', image)
        self.assertEqual(upload.value.status, 200)
        self.assertEqual(upload.value.headers['content-type'], 'application/json')
        page.wait_for_function("() => /upfiles/.test((document.querySelector('#image-url') || {}).value || '')")
        self.assertTrue(page.evaluate('window.uploadControlsDisabled'))
        self.assertFalse(page.is_disabled('#file-upload'))
        url = page.input_value('#image-url')
        self.assertRegex(url, r'/upfiles/.+\.png$')
        stored = [f for f in os.listdir(folder) if f != 'plot-photo.png']
        self.assertEqual(len(stored), 1)
        self.assertEqual(page.text_content('.wmd-upload-status'), '')

        # OK puts the image into the post.
        page.click('.wmd-prompt-dialog input[type=button][value=OK]')
        page.wait_for_selector('.wmd-prompt-dialog', state='detached')
        self.assertIn(url, page.input_value('#editor'))

        # The uploaded file is served (from the UPFILES_FOLDER setting).
        path = '/upfiles/' + url.split('/upfiles/', 1)[1]
        served = page.evaluate("""async path => {
            const r = await fetch(path);
            return [r.status, r.headers.get('content-type'), (await r.arrayBuffer()).byteLength];
        }""", path)
        # Re-encoded by Pillow: what is served is the stored copy, not the original.
        self.assertEqual(served, [200, 'image/png', os.path.getsize(os.path.join(folder, stored[0]))])
        self.check_page(page)

    def assert_upload_refused(self, page, upload, status, message):
        self.assertEqual(upload.value.status, status)
        error = page.locator('.wmd-prompt-dialog .upload-error')
        error.wait_for()
        self.assertIn(message, error.text_content())
        self.assertEqual(error.get_attribute('role'), 'alert')
        # Nothing was put in the address box, and the dialog can be used again.
        self.assertEqual(page.input_value('#image-url'), 'http://')
        self.assertFalse(page.is_disabled('#file-upload'))
        self.assertFalse(page.is_disabled('.wmd-prompt-dialog input[type=button][value=OK]'))
        # The browser logs the refused request; that is expected here.
        self.assertEqual([p for p in page.problems if '%d' % status not in p], [])
        page.problems.clear()
        self.check_page(page)

    def test_upload_too_big_is_refused(self):
        folder = self.upload_folder()
        image = os.path.join(folder, 'big-photo.png')
        tiny_png(image)
        with open(image, 'ab') as f:
            f.write(b'\0' * 4096)
        self.set_setting(settings.ALLOW_MAX_FILE_SIZE, 0.001)  # about 1 KB

        page = self.open_image_dialog(folder)
        with page.expect_response(lambda r: r.url.endswith('/upload/')) as upload:
            page.set_input_files('#file-upload', image)
        self.assert_upload_refused(page, upload, 413, 'maximum upload file size')
        self.assertEqual(os.listdir(folder), ['big-photo.png'])

    def test_upload_of_other_files_is_refused(self):
        folder = self.upload_folder()
        page = self.open_image_dialog(folder)
        with page.expect_response(lambda r: r.url.endswith('/upload/')) as upload:
            page.set_input_files('#file-upload', files=[{
                'name': 'notes.png', 'mimeType': 'image/png', 'buffer': b'<html><script>alert(1)</script></html>'}])
        self.assert_upload_refused(page, upload, 400, 'allowed file types')
        self.assertEqual(os.listdir(folder), [])

    def test_upload_needs_reputation(self):
        folder = self.upload_folder()
        image = os.path.join(folder, 'plot-photo.png')
        tiny_png(image)
        self.bob.reputation = 1
        self.bob.save()
        page = self.open_image_dialog(folder)
        with page.expect_response(lambda r: r.url.endswith('/upload/')) as upload:
            page.set_input_files('#file-upload', image)
        self.assert_upload_refused(page, upload, 403, 'reputation')

    def test_edit_answer(self):
        answer = self.create_answer(self.bob, self.question, 'Use the CSV export in the designer menu.')
        page = self.open(self.bob)
        self.visit(page, reverse('edit_answer', kwargs={'id': answer.id}))
        page.fill('#editor', 'Use the CSV export in the designer menu: File > Export > CSV.')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)
        self.assertIn('File &gt; Export &gt; CSV', Answer.objects.get(id=answer.id).body)  # stored as HTML
        self.assertIn('File &gt; Export &gt; CSV', page.content())

    def test_delete_own_comment(self):
        comment = self.create_comment(self.bob, self.question, 'Which version of Collect do you use?')
        page = self.open(self.bob)
        self.visit(page, self.question.get_absolute_url())
        page.click('#comment-%d-delete' % comment.id)
        self.confirm_dialog(page)
        self.assertTrue(Comment.objects.get(id=comment.id).nis.deleted)
