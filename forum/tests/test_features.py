from django.urls import reverse
from forum.models import Question
from forum.models.node import normalize_fences
from forum.utils.html import html2text, sanitize_html

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
        self.assertContains(response, '/media/vendor/highlight-11.11.1/highlight.min.js')
        self.assertContains(response, '/media/js/osqa.highlight.js')


class SanitizerTest(OSQATestCase):
    def test_unsafe_markup_is_removed(self):
        self.assertEqual(sanitize_html('<p onclick="x()">a &amp; b<script>evil()</script></p>'),
                         '<p>a &amp; b</p>')
        self.assertEqual(sanitize_html('<a href="javascript:alert(1)">x</a>'), '<a>x</a>')
        self.assertEqual(sanitize_html('<iframe src="/x"></iframe><blink>hi</blink>'), 'hi')

    def test_safe_markup_is_kept(self):
        html = '<a href="/questions/1/">q</a> <img src="/upfiles/a.png" alt="a"> <table><tr><td>1</td></tr></table>'
        self.assertEqual(sanitize_html(html),
                         '<a href="/questions/1/">q</a> <img src="/upfiles/a.png" alt="a"> '
                         '<table><tbody><tr><td>1</td></tr></tbody></table>')
        self.assertEqual(sanitize_html('<a href="/u" class="mention">@u</a>'),
                         '<a href="/u" class="mention">@u</a>')
        self.assertEqual(sanitize_html('<a href="/u" class="evil">@u</a>'), '<a href="/u">@u</a>')

    def test_html2text_for_plain_text_email(self):
        text = html2text('<p>Hello <strong>bold</strong> <a href="http://x.org/">link</a></p><ul><li>one</li></ul>')
        self.assertIn('Hello **bold** [link](http://x.org/)', text)
        self.assertIn('* one', text)


class EditorPreviewTest(OSQATestCase):
    def preview(self, text):
        return self.client.post(reverse('markdown_preview'), {'text': text})

    def test_preview_matches_saved_post_rendering(self):
        bob = self.create_user('bob')
        text = 'Hi @bob, see:\n\n```python\nprint(1)\n    ```\n\n<script>x()</script>*done*'
        response = self.preview(text)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<code class="language-python">print(1)', html)
        self.assertIn('data-user="%d"' % bob.id, html)
        self.assertNotIn('<script', html)
        question = Question(author=bob, title='A question title long enough', tagnames='gis', body=text)
        question.save()
        self.assertEqual(html, question.body)

    def test_preview_limits(self):
        self.assertEqual(self.preview('   ').content, b'')
        self.assertEqual(self.client.get(reverse('markdown_preview')).status_code, 405)
        self.assertEqual(self.preview('x' * 100001).status_code, 413)

    def test_editor_pages_point_the_preview_at_the_server(self):
        response = self.client.get(reverse('ask'))
        self.assertContains(response, 'data-preview-url="%s"' % reverse('markdown_preview'))
        self.assertNotContains(response, 'showdown.js')


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


class MentionTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', email_isvalid=True)
        self.bob = self.create_user('bob', email_isvalid=True)
        self.john = self.create_user('John Smith', email_isvalid=True)
        self.question = Question(author=self.alice, title='How do I reproject a shapefile?', tagnames='gis',
                                 body='Question body text that is long enough.')
        self.question.save()

    def link(self, text):
        from forum.utils.mentions import link_mentions
        return link_mentions(text)

    def test_linking(self):
        html, users = self.link('<p>Thanks @Bob and @John Smith said, ask @nobody or mail me@alice.org</p>')
        self.assertEqual({u.username for u in users}, {'bob', 'John Smith'})
        self.assertIn('<a class="mention" data-user="%d" href="%s">@bob</a> and' % (self.bob.id, self.bob.get_profile_url()), html)
        self.assertIn('>@John Smith</a> said', html)
        self.assertIn('@nobody', html)
        self.assertIn('me@alice.org', html)

    def test_no_prefix_matches_and_no_code(self):
        html, users = self.link('<p>@bo and @bobby</p><pre><code>@bob.route("/")</code></pre><p><a href="/x">@bob</a></p>')
        self.assertEqual(users, [])

    def test_mentions_cannot_be_faked_with_raw_html(self):
        from forum.models import Answer
        answer = Answer(author=self.alice, parent=self.question,
                        body='<a class="mention" data-user="%d" href="/x">hi</a> long enough answer' % self.bob.id)
        answer.save()
        self.assertNotIn('data-user', answer.body)

    def answer(self, user, text):
        from forum.models import Answer
        self.login(user)
        response = self.client.post('/questions/%d/answer/' % self.question.id, {'text': text})
        self.assertEqual(response.status_code, 302)
        return Answer.objects.filter(parent=self.question).latest('id')

    def mentioned(self):
        return {m[0].username for m in self.sent_mail if 'mentioned you' in m[1]}

    def test_comment_mention_notifies_once(self):
        from forum.models import QuestionSubscription, Comment
        QuestionSubscription(question=self.question, user=self.bob).save()
        self.sent_mail.clear()
        self.login(self.alice)
        reply = self.command('/comment/%d/' % self.question.id, {'comment': '@bob could you check the projection settings?'})
        self.assertTrue(reply['success'], reply)
        to_bob = [m for m in self.sent_mail if m[0] == self.bob]
        self.assertEqual(len(to_bob), 1)
        self.assertIn('alice mentioned you in: How do I reproject a shapefile?', to_bob[0][1])
        self.assertIn('class="mention"', Comment.objects.latest('id').body)

    def test_preference_and_self_mentions(self):
        self.bob.subscription_settings.notify_reply_to_comments = False
        self.bob.subscription_settings.save()
        self.sent_mail.clear()
        self.answer(self.alice, 'As @alice I agree with @bob, and @John Smith knows more.')
        self.assertEqual(self.mentioned(), {'John Smith'})

    def test_edit_notifies_only_new_mentions(self):
        answer = self.answer(self.alice, 'First version mentioning @bob only, long enough.')
        self.sent_mail.clear()
        response = self.client.post('/answers/%d/edit/' % answer.id, {
            'text': 'Second version mentioning @bob and @John Smith, long enough.', 'summary': 'more', 'revision': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.mentioned(), {'John Smith'})

    def test_matching_users_prefix_first(self):
        self.create_user('jumbo')
        inactive = self.create_user('bobsleigh')
        inactive.is_active = False
        inactive.save()
        lines = self.client.get('/matching_users/', {'q': 'bo'}).content.decode().splitlines()
        self.assertEqual(lines[0].split('|')[3], 'bob')
        self.assertNotIn('bobsleigh', '\n'.join(lines))
        self.assertTrue(any(line.endswith('|jumbo') for line in lines))


class TagSynonymTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        from forum.models import Tag
        self.mod = self.create_user('mod', reputation=10000)
        self.mod.is_staff = True
        self.mod.save()
        self.alice = self.create_user('alice', reputation=500)
        self.question = Question(author=self.alice, title='How do I reproject a shapefile?', tagnames='gis-analysis qgis',
                                 body='Question body text that is long enough.')
        self.question.save()
        self.gis = Question(author=self.alice, title='Which GIS for forest maps?', tagnames='gis',
                            body='Another question body that is long enough.')
        self.gis.save()
        self.tag = Tag.objects.get(name='gis')

    def edit(self, **data):
        self.login(self.mod)
        return self.client.post('/tags/gis/edit/', data)

    def test_description(self):
        response = self.edit(action='description', description='Geographic information systems, e.g. **QGIS**.')
        self.assertRedirects(response, '/tags/gis/', fetch_redirect_response=False)
        page = self.client.get('/tags/gis/')
        self.assertContains(page, '<strong>QGIS</strong>')
        self.assertContains(self.client.get('/tags/'), 'Geographic information systems, e.g. QGIS.')

    def test_merging_an_existing_tag(self):
        from forum.models import Tag, MarkedTag
        MarkedTag(tag=Tag.objects.get(name='gis-analysis'), user=self.alice, reason='good').save()
        self.edit(action='add_synonym', synonym='gis-analysis')

        question = Question.objects.get(id=self.question.id)
        self.assertEqual(question.tagname_list(), ['gis', 'qgis'])
        self.assertEqual(question.active_revision.tagnames, 'gis qgis')
        self.assertEqual(Tag.objects.get(name='gis').used_count, 2)
        self.assertFalse(Tag.active.filter(name='gis-analysis').exists())
        self.assertTrue(MarkedTag.objects.filter(tag__name='gis', user=self.alice).exists())
        self.assertRedirects(self.client.get('/tags/gis-analysis/'), '/tags/gis/', status_code=301,
                             fetch_redirect_response=False)

    def test_new_questions_use_the_target_tag(self):
        self.edit(action='add_synonym', synonym='arcgis')
        self.login(self.alice)
        self.client.post('/questions/ask/', {'title': 'Is arcgis able to read GeoPackages?',
                                             'text': 'Some body text that is long enough to pass validation',
                                             'tags': 'arcgis GIS geopackage'})
        question = Question.objects.get(title='Is arcgis able to read GeoPackages?')
        self.assertEqual(question.tagname_list(), ['gis', 'geopackage'])

    def test_invalid_synonyms(self):
        from forum.models import TagSynonym
        self.edit(action='add_synonym', synonym='arcgis')
        for name in ('gis', 'arcgis', 'bad name!', ''):
            with self.subTest(name=name):
                response = self.edit(action='add_synonym', synonym=name)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="tag-edit-error"')
        self.assertEqual(TagSynonym.objects.count(), 1)

    def test_only_moderators(self):
        self.login(self.alice)
        self.assertEqual(self.client.get('/tags/gis/edit/').status_code, 404)
        self.assertEqual(self.client.post('/tags/gis/edit/', {'action': 'description', 'description': 'x'}).status_code, 404)
        self.assertNotContains(self.client.get('/tags/gis/'), '/tags/gis/edit/')


class AccountDataTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        from forum.models import Answer, AuthKeyUserAssociation
        self.alice = self.create_user('alice', real_name='Alice Liddell', location='Rome', email_isvalid=True)
        self.bob = self.create_user('bob')
        self.question = Question(author=self.alice, title='How do I reproject a shapefile?', tagnames='gis',
                                 body='Question body text that is long enough.')
        self.question.save()
        self.answer = Answer(author=self.bob, parent=self.question, body='An answer by bob that is long enough.')
        self.answer.save()
        AuthKeyUserAssociation(user=self.alice, key='github:42', provider='github').save()

    def test_only_the_owner(self):
        url = '/account/%d/data/' % self.alice.id
        self.assertEqual(self.client.get(url).status_code, 404)
        admin = self.create_user('admin', is_superuser=True)
        self.login(admin)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + 'export/').status_code, 404)
        self.assertNotContains(self.client.get(self.alice.get_profile_url()), 'your data and account')

        self.login(self.alice)
        self.assertContains(self.client.get(self.alice.get_profile_url()), 'your data and account')
        self.assertContains(self.client.get(url), 'Download my data')

    def test_export(self):
        import json
        self.alice.reputation = 1000
        self.alice.save()
        self.login(self.alice)
        # The first up vote awards Supporter, whose name is a lazy translation string.
        self.assertTrue(self.command('/vote/%d/up/' % self.answer.id)['success'])
        response = self.client.get('/account/%d/data/export/' % self.alice.id)
        self.assertEqual(response['Content-Type'], 'application/json; charset=utf-8')
        self.assertIn('attachment;', response['Content-Disposition'])
        data = json.loads(response.content)
        self.assertEqual(data['profile']['email'], 'alice@example.com')
        self.assertEqual(data['profile']['real_name'], 'Alice Liddell')
        self.assertEqual(data['questions'][0]['title'], 'How do I reproject a shapefile?')
        self.assertEqual(data['questions'][0]['text'], 'Question body text that is long enough.')
        self.assertEqual(data['answers'], [])
        self.assertEqual(data['login_methods'], ['github'])
        self.assertIn('Supporter', [b['badge'] for b in data['badges']])
        self.assertEqual(data['votes'][0]['vote'], 'voteup')

    def delete(self, **data):
        self.login(self.alice)
        return self.client.post('/account/%d/data/delete/' % self.alice.id, data)

    def test_delete_needs_confirmation(self):
        from forum.models import User
        for data in ({}, {'confirm_username': 'Alice', 'password': 'secret-pass'},
                     {'confirm_username': 'alice', 'password': 'wrong'}):
            with self.subTest(data=data):
                self.delete(**data)
                self.assertTrue(User.objects.get(id=self.alice.id).is_active)
        self.assertEqual(self.client.get('/account/%d/data/delete/' % self.alice.id).status_code, 302)
        self.assertTrue(User.objects.get(id=self.alice.id).is_active)

    def test_delete_anonymises_and_keeps_posts(self):
        from forum.models import User, AuthKeyUserAssociation
        response = self.delete(confirm_username='alice', password='secret-pass')
        self.assertRedirects(response, '/', fetch_redirect_response=False)

        user = User.objects.get(id=self.alice.id)
        self.assertEqual(user.username, 'deleted-user-%d' % user.id)
        self.assertEqual((user.email, user.real_name, user.location), ('', '', ''))
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(AuthKeyUserAssociation.objects.filter(user=user).exists())

        # Signed out, the question is still there under the new name.
        self.assertNotIn('_auth_user_id', self.client.session)
        page = self.client.get(self.question.get_absolute_url())
        self.assertContains(page, 'Question body text that is long enough.')
        self.assertContains(page, 'deleted-user-%d' % user.id)
        self.assertNotContains(page, 'Alice Liddell')
        self.assertFalse(self.client.login(username='alice', password='secret-pass'))

    def test_last_administrator_cannot_delete(self):
        from forum.models import User
        self.alice.is_superuser = True
        self.alice.save()
        self.delete(confirm_username='alice', password='secret-pass')
        self.assertTrue(User.objects.get(id=self.alice.id).is_active)
