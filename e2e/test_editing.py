"""Editor details: form validation, image upload, editing answers and
deleting comments."""
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

    def test_upload_an_image(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        self.set_setting(settings.UPFILES_FOLDER, folder)
        image = os.path.join(folder, 'plot-photo.png')
        tiny_png(image)

        page = self.open(self.bob)
        self.visit(page, self.question.get_absolute_url())
        page.click('#wmd-image-button')
        page.wait_for_selector('#file-upload')
        with page.expect_response(lambda r: r.url.endswith('/upload/')) as upload:
            page.set_input_files('#file-upload', image)
        self.assertEqual(upload.value.status, 200)
        page.wait_for_function("() => /upfiles/.test((document.querySelector('#image-url') || {}).value || '')")
        url = page.input_value('#image-url')
        self.assertRegex(url, r'/upfiles/.+\.png$')
        self.assertEqual(len([f for f in os.listdir(folder) if f != 'plot-photo.png']), 1)
        self.check_page(page)

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
