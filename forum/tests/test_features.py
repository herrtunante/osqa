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
