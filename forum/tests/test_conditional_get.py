"""ETag / 304 for anonymous question pages and the feed; lazy images in posts."""
from forum.models import Answer, Question
from forum.models.node import render_markdown
from forum.utils.html import lazy_images

from .base import OSQATestCase


class ConditionalQuestionPageTest(OSQATestCase):

    def setUp(self):
        super().setUp()
        self.author = self.create_user('author', reputation=100)
        self.question = Question(author=self.author, title='A question about caching', body='Body text here',
                                 tagnames='cache')
        self.question.save()
        self.url = self.question.get_absolute_url()

    def counted_view(self):
        """First visit (counted, no ETag yet), then the page with its ETag."""
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn('ETag', first)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        return page

    def test_revalidated_copy_is_not_modified(self):
        page = self.counted_view()
        etag = page['ETag']
        self.assertIn('private', page['Cache-Control'])
        self.assertIn('no-cache', page['Cache-Control'])
        self.assertIn('Cookie', page['Vary'])
        self.assertIn('Accept-Language', page['Vary'])

        response = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response['ETag'], etag)
        self.assertEqual(response.content, b'')

    def test_new_answer_changes_the_etag(self):
        etag = self.counted_view()['ETag']
        Answer(author=self.author, parent=self.question, body='A new answer long enough').save()
        response = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A new answer long enough')

    def test_language_changes_the_etag(self):
        etag = self.counted_view()['ETag']
        response = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag, HTTP_ACCEPT_LANGUAGE='fr')
        self.assertEqual(response.status_code, 200)

    def test_signed_in_users_never_get_one(self):
        self.client.force_login(self.author)
        self.client.get(self.url)
        response = self.client.get(self.url)
        self.assertNotIn('ETag', response)

    def test_pending_message_disables_it(self):
        etag = self.counted_view()['ETag']
        session = self.client.session
        from django.contrib.messages.storage.session import SessionStorage
        session[SessionStorage.session_key] = '[["__json_message",0,20,"Hello there"]]'
        session.save()
        response = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response.status_code, 200)


class ConditionalFeedTest(OSQATestCase):

    def test_feed_not_modified(self):
        Question(author=self.create_user('alice'), title='Feed question title', body='Body text').save()
        self.client.get('/feeds/rss/')  # stores the defaults of the settings it reads
        first = self.client.get('/feeds/rss/')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.client.get('/feeds/rss/', HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)


class StaleSettingValuesTest(OSQATestCase):
    """Setting values an earlier test left in memory used to expire in the
    middle of a test: the setting then stored its default, the KeyValue time
    stamp moved and the feed's ETag changed between two requests (an
    intermittent CI failure of test_feed_not_modified)."""

    def setUp(self):
        from datetime import datetime
        from forum.settings.base import Setting
        for setting_set in Setting.sets.values():
            for setting in setting_set:
                setting._temp = (setting.default, datetime.now())  # left by an earlier test
        super().setUp()

    def test_feed_etag_holds_when_they_would_have_expired(self):
        from datetime import datetime
        from forum.settings.base import Setting
        Question(author=self.create_user('alice'), title='Feed question title', body='Body text').save()
        self.client.get('/feeds/rss/')
        for setting_set in Setting.sets.values():  # TMP_MINICACHE_SECONDS pass
            for setting in setting_set:
                if setting._temp:
                    setting._temp = (setting._temp[0], datetime(2000, 1, 1))
        first = self.client.get('/feeds/rss/')
        self.assertEqual(self.client.get('/feeds/rss/', HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)


class LazyImagesTest(OSQATestCase):

    def test_post_images_load_lazily(self):
        html = render_markdown('Look: ![a cat](http://example.com/cat.png "Cat")')
        self.assertIn('<img loading="lazy" decoding="async" ', html)
        self.assertIn('src="http://example.com/cat.png"', html)

    def test_users_cannot_set_loading_attributes(self):
        html = render_markdown('<img src="http://example.com/x.png" loading="eager" decoding="sync" '
                               'fetchpriority="high" onerror="alert(1)">')
        self.assertEqual(html.count('loading='), 1)
        self.assertEqual(html.count('decoding='), 1)
        self.assertIn('loading="lazy"', html)
        self.assertIn('decoding="async"', html)
        self.assertNotIn('eager', html)
        self.assertNotIn('sync"', html.replace('async"', ''))
        self.assertNotIn('fetchpriority', html)
        self.assertNotIn('onerror', html)

    def test_code_is_left_alone(self):
        html = render_markdown('`<img src="x">`')
        self.assertNotIn('loading=', html)
        self.assertEqual(lazy_images('<p>no images</p>'), '<p>no images</p>')

    def test_avatars_are_lazy_with_dimensions(self):
        from forum.templatetags.extra_tags import gravatar
        html = gravatar(self.create_user('bob'), 32)
        for part in ('width="32"', 'height="32"', 'loading="lazy"', 'decoding="async"'):
            self.assertIn(part, html)
