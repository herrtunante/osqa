"""People and the pages around them: profile tabs (questions, activity,
reputation, votes, favourites, preferences), reporting a user, awarding
reputation, badges, the contact form, Markdown help and OpenSearch, the
machine endpoints agents read (llms.txt, Markdown, JSONL, RSS, MCP), the
notification bell, email validation, and the AI assistant and suggestions."""
import json
import re
from urllib.parse import urlsplit
from unittest import mock

from django.urls import reverse

from forum.models import AISuggestion, Notification, User
from forum.models.action import Action

from .base import E2ETestCase

# Runs fetch() in the page, so the browser itself makes the request.
FETCH = '''async ([url, method, body]) => {
    const options = {method: method, credentials: 'same-origin'};
    if (body !== null) {
        options.body = body;
        options.headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'};
    }
    const response = await fetch(url, options);
    return {status: response.status, type: response.headers.get('Content-Type') || '', text: await response.text()};
}'''


class AgentFetchMixin:
    def fetch(self, page, path, method='GET', body=None):
        result = page.evaluate(FETCH, [path, method, None if body is None else json.dumps(body)])
        self.assertLess(result['status'], 500, '%s %s' % (method, path))
        return result

    def follow(self, page, selector):
        """Opens the page a link points to (links that open a new tab, or <link> elements)."""
        href = page.locator(selector).first.get_attribute('href')
        return self.visit(page, href if href.startswith('/') else href[len(self.live_server_url):] or href)


class ProfileTabsTest(AgentFetchMixin, E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=100)
        self.carol = self.create_user('carol', reputation=100)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')
        self.create_answer(self.bob, self.question, 'Use the CSV export in the Collect designer.')

    def open_tab(self, page, tab):
        with page.expect_navigation():
            page.click('.tabsA a#%s' % tab)
        self.check_page(page)

    def test_votes_favourites_reputation_and_activity_tabs(self):
        title = self.question.title
        # carol votes the question up and makes it a favourite
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.click_command(page, '#post-%d-upvote' % self.question.id)
        self.click_command(page, '#favorite-mark')

        # her own profile shows both; the votes tab is hers only
        self.visit(page, self.carol.get_profile_url())
        self.open_tab(page, 'votes')
        self.assertIn(reverse('user_votes', kwargs={'id': self.carol.id, 'slug': 'carol'}), page.url)
        self.assertIn(title, page.locator('body').inner_text())
        self.open_tab(page, 'favorites')
        self.assertIn(title, page.locator('body').inner_text())

        # alice's profile, seen by carol: no votes tab; reputation and activity are public
        self.visit(page, self.alice.get_profile_url())
        self.assertEqual(page.locator('.tabsA a#votes').count(), 0)
        self.open_tab(page, 'reputation')
        # the vote's points are listed, and the graph is drawn
        self.assertIn(title, page.locator('.reputation-list').inner_text())
        self.assertIn('10', page.locator('.reputation-list .positiveReputation').first.inner_text())
        page.wait_for_selector('#diagram canvas')
        self.assertIn(title, page.content())
        self.open_tab(page, 'recent')
        self.assertIn(title, page.locator('body').inner_text())

        # the questions she asked, from the feed link on her overview
        self.open_tab(page, 'stats')
        asked = page.locator('#questionsBlock a.feed-icon').get_attribute('href')
        self.assertTrue(asked.endswith('?type=rss'), asked)
        rss = self.fetch(page, asked)
        self.assertIn(title, rss['text'])
        self.visit(page, asked.split('?')[0])
        self.assertIn('Questions asked by alice', page.locator('body').inner_text())
        self.assertIn(title, page.locator('body').inner_text())
        # and the ones bob answered
        self.visit(page, self.bob.get_answered_url())
        self.assertIn(title, page.locator('body').inner_text())

    def test_preferences_are_saved(self):
        page = self.open(self.carol)
        self.visit(page, self.carol.get_profile_url())
        page.click('#user-menu')
        with page.expect_navigation():
            page.click('#user-menu-dropdown a[href="%s"]' % reverse(
                'user_preferences', kwargs={'id': self.carol.id, 'slug': 'carol'}))
        self.check_page(page)
        self.assertFalse(page.is_checked('#id_sticky_sorts'))
        page.check('#id_sticky_sorts')
        with page.expect_navigation():
            page.click('input[name=save]')
        self.check_page(page)
        self.assertIn('New preferences saved', page.locator('body').inner_text())
        self.assertEqual(User.objects.get(id=self.carol.id).prop.preferences,
                         {'sticky_sorts': True, 'keyboard_shortcuts': True, 'answer_requests': True})
        # still ticked when she comes back
        self.visit(page, reverse('user_preferences', kwargs={'id': self.carol.id, 'slug': 'carol'}))
        self.assertTrue(page.is_checked('#id_sticky_sorts'))

    def test_report_a_user(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.open(self.carol)
        self.visit(page, self.bob.get_profile_url())
        page.click('a[href="%s"]' % reverse('user_report', kwargs={'id': self.bob.id}))
        self.confirm_dialog(page, {'textarea[name=publicmsg]': 'Posts links to a shop in every answer <a href=x>'})
        report = Action.objects.get(action_type='report', user=self.carol)
        self.assertEqual(report.extra['publicmsg'], 'Posts links to a shop in every answer <a href=x>')
        mail = self.mail_to(admin)[-1]  # the administrators are told
        self.assertIn('User reported: bob by carol', mail[1])
        self.assertIn('Posts links to a shop in every answer <a href=x>', mail[3])  # plain text as typed
        self.assertIn('every answer &lt;a href=x&gt;', mail[2])  # never markup in the HTML part
        # nobody reports from their own profile
        self.visit(page, self.carol.get_profile_url())
        self.assertEqual(page.locator('a[href="%s"]' % reverse('user_report', kwargs={'id': self.carol.id})).count(), 0)
        # the administrator finds the report in carol's activity
        admin_page = self.open(admin)
        self.visit(admin_page, self.carol.get_profile_url())
        self.open_tab(admin_page, 'recent')
        self.assertIn('Posts links to a shop in every answer', admin_page.locator('body').inner_text())

    def test_award_points_on_a_phone(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.open(admin, device='mobile', dark=True)
        self.visit(page, self.bob.get_profile_url())
        page.click('#user-menu')
        page.click('#award-rep-points')
        self.confirm_dialog(page, {'#points-to-award': '25', '#award-message': 'Thanks for the CSV answers'})
        self.assertEqual(User.objects.get(id=self.bob.id).reputation, 125)
        self.assertEqual(page.locator('#user-reputation').inner_text().strip(), '125')
        self.visit(page, self.bob.get_profile_url())
        self.assertEqual(page.locator('#user-reputation').inner_text().strip(), '125')


class SitePagesTest(AgentFetchMixin, E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)
        self.carol = self.create_user('carol', reputation=100)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')

    def test_badge_page(self):
        # carol's first up vote earns her the Supporter badge
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.click_command(page, '#post-%d-upvote' % self.question.id)
        self.visit(page, '/badges/')
        with page.expect_navigation():
            page.locator('a.medal', has_text='Supporter').first.click()
        self.check_page(page)
        self.assertIn('First up vote', page.locator('#main-body').inner_text())
        self.assertIn('carol', page.locator('#award-list').inner_text())

    def test_contact_form(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.open()
        self.visit(page, '/faq/')
        with page.expect_navigation():
            page.locator('a[href^="%s"]' % reverse('feedback')).first.click()
        self.check_page(page)
        page.fill('#id_name', 'Dana')
        page.fill('#id_email', 'dana@example.com')
        page.fill('#id_message', 'The CSV export link on the FAQ page is broken.')
        with page.expect_navigation():
            page.get_by_role('button', name='Send Feedback').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/faq/'), page.url)
        self.assertIn('Thanks for the feedback', page.locator('body').inner_text())
        mails = self.mail_to(admin)
        self.assertEqual(len(mails), 1)
        self.assertIn('The CSV export link on the FAQ page is broken.', mails[0][3])
        self.assertIn('dana@example.com', mails[0][3])

    def test_markdown_help_and_opensearch(self):
        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        self.follow(page, 'a[href="%s"]' % reverse('markdown_help'))
        self.assertIn('Markdown', page.locator('body').inner_text())
        self.visit(page, '/')
        description = self.fetch(page, page.locator('link[rel=search]').get_attribute('href'))
        self.assertEqual(description['status'], 200)
        self.assertIn('OpenSearchDescription', description['text'])
        self.assertIn('/search/', description['text'])


class AgentEndpointsTest(AgentFetchMixin, E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=100)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.create_answer(self.bob, self.question, 'Use the **CSV export** in the Collect designer.')
        self.unanswered = self.create_question(self.bob, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')

    def test_llms_txt_and_the_links_in_it(self):
        page = self.open()
        response = page.goto(self.url('/llms.txt'))
        self.assertEqual(response.status, 200)
        text = page.locator('body').inner_text()
        self.assertIn(self.question.title, text)
        path = lambda pattern: re.search(r'https?://[^/\s)]+(%s)' % pattern, text).group(1)

        # one thread as Markdown
        md = self.fetch(page, path(r'/questions/\d+\.md'))
        self.assertIn('text/markdown', md['type'])
        self.assertIn('# %s' % self.question.title, md['text'])
        self.assertIn('Use the **CSV export** in the Collect designer.', md['text'])
        # every answered question as JSON Lines
        lines = self.fetch(page, path(r'/export/qa\.jsonl'))['text'].splitlines()
        records = [json.loads(line) for line in lines]
        self.assertEqual([r['title'] for r in records], [self.question.title])
        self.assertEqual(records[0]['answers'][0]['author'], 'bob')
        # the latest questions feed
        rss = self.fetch(page, path(r'/feeds/rss/'))
        self.assertIn('<rss', rss['text'])
        self.assertIn(self.unanswered.title, rss['text'])
        self.check_page(page)

    def test_markdown_link_of_a_question_page(self):
        page = self.open()
        self.visit(page, self.question.get_absolute_url())
        md = self.fetch(page, page.locator('link[rel=alternate][type="text/markdown"]').get_attribute('href'))
        self.assertEqual(md['status'], 200)
        self.assertIn('status: "answered"', md['text'])

    def test_mcp_server(self):
        page = self.open()
        self.visit(page, '/')
        call = lambda id, method, params=None: json.loads(self.fetch(page, '/mcp', 'POST', dict(
            jsonrpc='2.0', id=id, method=method, params=params or {}))['text'])

        init = call(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                      'clientInfo': {'name': 'e2e', 'version': '1'}})
        self.assertEqual(init['result']['protocolVersion'], '2025-06-18')
        self.assertIn('tools', init['result']['capabilities'])
        # the notification that follows needs no answer
        self.assertEqual(self.fetch(page, '/mcp', 'POST', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                         ['status'], 202)

        tools = call(2, 'tools/list')['result']['tools']
        self.assertEqual(sorted(t['name'] for t in tools), ['get_question', 'search_forum'])

        found = call(3, 'tools/call', {'name': 'search_forum', 'arguments': {'query': 'export CSV'}})['result']
        self.assertFalse(found['isError'])
        results = json.loads(found['content'][0]['text'])['results']
        self.assertEqual(results[0]['id'], self.question.id)
        self.assertEqual(results[0]['status'], 'answered')

        thread = call(4, 'tools/call', {'name': 'get_question', 'arguments': {'id': self.question.id}})['result']
        self.assertIn('Use the **CSV export** in the Collect designer.', thread['content'][0]['text'])
        missing = call(5, 'tools/call', {'name': 'get_question', 'arguments': {'id': 999999}})['result']
        self.assertTrue(missing['isError'])
        # GET is not part of this server
        self.assertEqual(self.fetch(page, '/mcp')['status'], 405)
        page.problems.clear()  # the browser logs the 405


class NotificationBellTest(E2ETestCase):
    def test_unread_count_and_mark_all_read(self):
        alice = self.create_user('alice', reputation=100)
        bob = self.create_user('bob', reputation=100)
        question = self.create_question(alice, 'How do I export Collect survey data to CSV?')
        page = self.open(alice)
        self.visit(page, '/')
        count = page.locator('#notification-bell .notification-count')
        self.assertTrue(count.is_hidden())

        # bob answers while alice has the page open; the bell checks again
        # when the tab comes back into view
        self.create_answer(bob, question)
        self.create_comment(bob, question)
        with page.expect_response(lambda r: r.url.endswith(reverse('notifications_count'))):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("document.querySelector('#notification-bell .notification-count').textContent === '2'")
        self.assertTrue(count.is_visible())
        self.assertIn('2 unread', page.get_attribute('#notification-bell', 'aria-label'))
        self.assertTrue(page.title().startswith('(2) '))

        with page.expect_navigation():
            page.click('#notification-bell')
        self.check_page(page)
        self.assertEqual(page.locator('.inbox-item.unread').count(), 2)
        with page.expect_navigation():
            page.get_by_role('button', name='Mark all as read').click()
        self.check_page(page)
        self.assertEqual(page.locator('.inbox-item.unread').count(), 0)
        self.assertEqual(page.locator('.inbox-item').count(), 2)
        self.assertFalse(Notification.objects.filter(user=alice, read_at=None).exists())
        self.assertTrue(count.is_hidden())


class EmailValidationTest(E2ETestCase):
    def test_validate_email_from_the_link_on_a_phone(self):
        dana = self.create_user('dana', reputation=10, email_isvalid=False)
        page = self.open(dana, device='mobile', dark=True)
        self.visit(page, dana.get_profile_url())
        self.assertIn('not validated', page.locator('.user-details').inner_text())
        with page.expect_navigation():
            page.click('a[href="%s"]' % reverse('send_validation_email'))
        self.check_page(page)
        # The confirmation page: where the link goes, and a button for a new one (a POST).
        self.assertIn('dana@example.com', page.locator('.auth-card').inner_text())
        with page.expect_navigation():
            page.click('.auth-card button[type=submit]')
        self.check_page(page)
        self.assertIn('We sent a new confirmation link', page.locator('body').inner_text())

        text = self.mail_to(dana)[-1][3]
        link = re.search(r'https?://\S+?(/account/validate/\d+/[^/\s]+/)', text).group(1)
        self.visit(page, link)
        self.assertEqual(urlsplit(page.url).path, link)
        self.assertIn('your email is now validated', page.locator('body').inner_text())
        self.assertTrue(User.objects.get(id=dana.id).email_isvalid)
        self.visit(page, dana.get_profile_url())
        self.assertNotIn('not validated', page.locator('.user-details').inner_text())


class AIAssistantTest(E2ETestCase):
    """The AI module with a stand-in for the Gemini client (the live server
    runs in this process, so the patch applies to its requests too)."""

    def setUp(self):
        super().setUp()
        from forum.tests.test_ai import AI_ENV
        from forum_modules.ai import settings as ai
        self._patch(mock.patch.dict('os.environ', {name: '' for name in AI_ENV}))
        self.set_setting(ai.GEMINI_API_KEY, 'test-gemini-key')
        self.set_setting(ai.AI_ASSISTANT, True)
        # only the assistant is under test here
        for setting in (ai.AI_SPAM_MODERATION, ai.AI_PROFILE_REVIEW, ai.AI_DUPLICATE_DETECTION):
            self.set_setting(setting, False)
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=100)
        self.export = self.create_question(self.bob, 'How do I export survey data to CSV?')
        self.create_answer(self.alice, self.export, 'Use the CSV export in the Collect designer.')

    def gemini(self, text):
        from forum.tests.test_assistant import FakeWriter
        fake = FakeWriter(text)
        self._patch(mock.patch('forum_modules.ai.client.get_client', return_value=fake))
        return fake

    def test_assistant_on_the_ask_page(self):
        fake = self.gemini('Open the survey in the designer and use the CSV export [1].')
        from forum_modules.ai import knowledge
        knowledge.sync('forum', knowledge.forum_documents())

        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        page.fill('#id_title', 'Exporting my survey data as CSV')
        page.fill('#editor', 'I need CSV files of my plots for R.')
        page.click('#ask-assistant-button')
        self.wait_for_ajax(page)
        answer = page.locator('#ask-assistant-answer')
        answer.locator('.ask-assistant-body').wait_for()
        self.check_page(page)
        self.assertIn('use the CSV export', answer.inner_text())
        source = answer.locator('.ai-suggestion-sources a').first
        self.assertEqual(source.inner_text(), self.export.title)
        self.assertTrue(source.get_attribute('href').endswith(self.export.get_absolute_url()))
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(len(fake.checks), 1)  # the fact check passed

    def test_suggestion_marked_helpful_then_removed(self):
        question = self.create_question(self.alice, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')
        suggestion = AISuggestion.objects.create(
            question=question, body='Use the time series recipe [1].', html='<p>Use the time series recipe.</p>',
            sources=[{'url': self.url(self.export.get_absolute_url()), 'title': self.export.title, 'kind': 'forum'}])

        # the asker marks it helpful
        page = self.open(self.alice)
        self.visit(page, question.get_absolute_url())
        section = page.locator('section.ai-suggestion')
        self.assertIn('Use the time series recipe.', section.inner_text())
        with page.expect_navigation():
            section.get_by_role('button', name='This helped').click()
        self.check_page(page)
        self.assertEqual(AISuggestion.objects.get(id=suggestion.id).status, AISuggestion.HELPFUL)
        self.assertIn('the asker found this helpful', section.inner_text())
        self.assertEqual(section.get_by_role('button', name='This helped').count(), 0)

        # others see it but can't act on it
        other = self.open(self.bob)
        self.visit(other, question.get_absolute_url())
        self.assertEqual(other.locator('section.ai-suggestion .ai-suggestion-actions').count(), 0)

        # a moderator removes it
        moderator = self.create_user('moderator', reputation=10, is_staff=True)
        page = self.open(moderator)
        self.visit(page, question.get_absolute_url())
        with page.expect_navigation():
            page.locator('section.ai-suggestion').get_by_role('button', name='Remove suggestion').click()
        self.check_page(page)
        suggestion = AISuggestion.objects.get(id=suggestion.id)
        self.assertEqual((suggestion.status, suggestion.resolved_by), (AISuggestion.REMOVED, moderator))
        self.assertEqual(page.locator('section.ai-suggestion').count(), 0)
