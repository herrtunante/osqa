from forum.models import Question
from forum.models.node import normalize_fences
from forum.utils.html import sanitize_html

from .base import OSQATestCase


class CodeBlockTest(OSQATestCase):
    def test_fenced_code_keeps_its_language(self):
        user = self.create_user('alice')
        question = Question(author=user, title='How do I group plots in R?', tagnames='r',
                            body='Try:\n\n```r\nx <- c(1, 2)  # <b>not bold</b>\n```\n')
        question.save()
        html = question.html
        self.assertIn('<pre><code class="language-r">', html)
        self.assertIn('&lt;b&gt;not bold&lt;/b&gt;', html)

    def test_indented_closing_fence(self):
        # The editor auto-indents the line after an indented code line.
        self.assertEqual(normalize_fences('```py\nif x:\n    y()\n    ```\nafter'),
                         '```py\nif x:\n    y()\n```\nafter')
        # Four spaces make an indented code block, not a fence.
        self.assertEqual(normalize_fences('    ```\n    code'), '    ```\n    code')

    def test_sanitizer_allows_only_language_classes(self):
        self.assertEqual(sanitize_html('<code class="language-python">x</code>'),
                         '<code class="language-python">x</code>')
        self.assertEqual(sanitize_html('<code class="evil" onclick="x()">x</code>'), '<code>x</code>')
        self.assertEqual(sanitize_html('<p class="language-python">x</p>'), '<p>x</p>')

    def test_highlighter_is_loaded(self):
        response = self.client.get('/')
        self.assertContains(response, '/media/vendor/highlight-11.10.0/highlight.min.js')
        self.assertContains(response, '/media/js/osqa.highlight.js')


class SearchMetadataTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob')
        self.question = Question(author=self.alice, title='Is </script><b>x</b> escaped?', tagnames='html',
                                 body='Question body text that is long enough.')
        self.question.save()

    def structured_data(self, response):
        import json, re
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_question_page_has_qapage_markup(self):
        from forum.models import Answer
        answer = Answer(author=self.bob, parent=self.question, body='An answer that is long enough to count.')
        answer.save()

        response = self.client.get(self.question.get_absolute_url())
        content = response.content.decode()
        # The title cannot close the script element early.
        self.assertNotIn('</script><b>', content)
        data = self.structured_data(response)
        self.assertEqual(data['@type'], 'QAPage')
        self.assertEqual(data['mainEntity']['name'], 'Is </script><b>x</b> escaped?')
        self.assertEqual(data['mainEntity']['answerCount'], 1)
        self.assertEqual(data['mainEntity']['suggestedAnswer'][0]['text'], 'An answer that is long enough to count.')
        self.assertTrue(data['mainEntity']['suggestedAnswer'][0]['url'].startswith('http'))

        self.assertContains(response, '<meta property="og:type" content="article" />')
        self.assertContains(response, '<meta property="article:tag" content="html" />')

    def test_other_pages_have_link_preview_tags(self):
        response = self.client.get('/tags/')
        self.assertContains(response, '<meta property="og:type" content="website" />')
        self.assertContains(response, '<meta property="og:url" content="http://testserver/tags/" />')
        self.assertContains(response, '<meta name="twitter:card" content="summary" />')


class ImageUploadTest(OSQATestCase):
    PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4'
           b'\x89\x00\x00\x00\rIDATx\xdac\xfc\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82')

    def setUp(self):
        super().setUp()
        import tempfile, shutil
        from forum import settings
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        settings.UPFILES_FOLDER.set_value(self.folder)
        self.addCleanup(setattr, settings.UPFILES_FOLDER, '_temp', None)

    def upload(self, content, name):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post('/upload/', {'file-upload': SimpleUploadedFile(name, content)})
        self.assertEqual(response['Content-Type'], 'application/xml')
        return response.content.decode()

    def test_image_is_stored(self):
        self.login(self.create_user('alice', reputation=500))
        reply = self.upload(self.PNG, 'screen shot.png')
        self.assertIn('<file_url>/upfiles/screen_shot.png</file_url>', reply)
        self.assertIn('<error><![CDATA[]]></error>', reply)

    def test_content_must_be_an_image(self):
        self.login(self.create_user('alice', reputation=500))
        reply = self.upload(b'<html><script>alert(1)</script></html>', 'evil.png')
        self.assertIn('<file_url></file_url>', reply)
        self.assertIn('allowed file types', reply)

    def test_low_reputation_is_refused(self):
        self.login(self.create_user('newbie', reputation=1))
        reply = self.upload(self.PNG, 'a.png')
        self.assertIn('<file_url></file_url>', reply)
        self.assertIn('reputation', reply)

    def test_editor_knows_the_upload_url(self):
        self.assertContains(self.client.get('/questions/ask/'), 'upload_url: "/upload/"')


class DuplicateTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        from forum.models import Answer, Comment
        from forum.actions import AcceptAnswerAction
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob')
        self.mod = self.create_user('mod', reputation=10000)
        self.mod.is_staff = True
        self.mod.save()

        self.original = Question(author=self.alice, title='How do I reproject a shapefile?', tagnames='gis',
                                 body='Original question body text.')
        self.original.save()
        self.duplicate = Question(author=self.bob, title='Reprojecting shapefiles to UTM?', tagnames='gis',
                                  body='Duplicate question body text.')
        self.duplicate.save()
        self.answer = Answer(author=self.alice, parent=self.duplicate, body='Use ogr2ogr with -t_srs for this.')
        self.answer.save()
        Comment(author=self.alice, parent=self.answer, body='This is a comment on the answer, long enough.').save()
        AcceptAnswerAction(user=self.bob, node=self.answer, ip='127.0.0.1').save()

    def close(self, user, **data):
        self.login(user)
        data.setdefault('prompt', 'Duplicate Question')
        return self.command('/questions/%d/close/' % self.duplicate.id, data)

    def refresh(self, node):
        return type(node).objects.get(id=node.id)

    def test_close_as_duplicate_links_the_original(self):
        reply = self.close(self.mod, duplicate_of='%s' % self.original.get_absolute_url())
        self.assertTrue(reply['success'], reply)
        duplicate = self.refresh(self.duplicate)
        self.assertEqual(duplicate.duplicate_of, self.original)

        page = self.client.get(duplicate.get_absolute_url())
        self.assertContains(page, 'This question already has an answer here:')
        self.assertContains(page, 'href="%s"' % self.original.get_absolute_url())
        self.assertContains(page, '[duplicate]')
        self.assertContains(self.client.get('/questions/'), '[duplicate]')
        # The answers stay where they were unless a moderator moves them.
        self.assertEqual(self.refresh(self.answer).parent.id, self.duplicate.id)

    def test_invalid_originals_are_refused(self):
        for value in ('not a number', '999999', str(self.duplicate.id)):
            with self.subTest(value=value):
                reply = self.close(self.mod, duplicate_of=value)
                self.assertFalse(reply['success'])
                self.assertFalse(self.refresh(self.duplicate).nis.closed)

    def test_moderator_can_move_the_answers(self):
        reply = self.close(self.mod, duplicate_of=str(self.original.id), move_answers='1')
        self.assertTrue(reply['success'], reply)

        answer = self.refresh(self.answer)
        self.assertEqual(answer.parent.id, self.original.id)
        self.assertEqual(answer.abs_parent.id, self.original.id)
        self.assertFalse(answer.nis.accepted)
        self.assertEqual(answer.comments.get().abs_parent.id, self.original.id)
        self.assertEqual(self.refresh(self.original).answer_count, 1)
        self.assertEqual(self.refresh(self.duplicate).answer_count, 0)
        self.assertContains(self.client.get(self.original.get_absolute_url()), 'Use ogr2ogr')

    def test_only_moderators_move_answers(self):
        closer = self.create_user('closer', reputation=100000)
        reply = self.close(closer, duplicate_of=str(self.original.id), move_answers='1')
        self.assertTrue(reply['success'], reply)
        self.assertEqual(self.refresh(self.answer).parent.id, self.duplicate.id)

    def test_reopening_removes_the_link(self):
        self.close(self.mod, duplicate_of=str(self.original.id))
        reply = self.command('/questions/%d/reopen/' % self.duplicate.id)
        self.assertTrue(reply['success'], reply)
        duplicate = self.refresh(self.duplicate)
        self.assertFalse(duplicate.nis.closed)
        self.assertIsNone(duplicate.duplicate_of)
        self.assertNotContains(self.client.get(duplicate.get_absolute_url()), '[duplicate]')

    def test_close_dialog_offers_the_fields(self):
        self.login(self.mod)
        dialog = self.client.get('/questions/%d/close/' % self.duplicate.id).content.decode()
        self.assertIn('name="duplicate_of"', dialog)
        self.assertIn('name="move_answers"', dialog)
