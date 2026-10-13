import json
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from django.urls import reverse

from forum.models import Question, Answer, ModerationReport

from .base import OSQATestCase


class FakeClaude:
    """Stands in for anthropic.Anthropic(): records requests, replays answers."""

    def __init__(self, *answers, stop_reason='end_turn', error=None):
        self.answers = list(answers)
        self.stop_reason = stop_reason
        self.error = error
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.requests.append(params)
        if self.error:
            raise self.error
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type='text', text=json.dumps(answer))])

    @property
    def prompt(self):
        return self.requests[-1]['messages'][0]['content']


class AITestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        from forum_modules.ai import settings as ai_settings
        self.ai = ai_settings
        self.set(ai_settings.ANTHROPIC_API_KEY, 'sk-ant-test')

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def claude(self, *answers, **kwargs):
        fake = FakeClaude(*answers, **kwargs)
        patcher = mock.patch('forum_modules.ai.client.get_client', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class ClaudeClientTest(AITestCase):
    SCHEMA = {'type': 'object', 'properties': {'x': {'type': 'integer'}}, 'required': ['x'],
              'additionalProperties': False}

    def ask(self):
        from forum_modules.ai.client import ask_claude
        return ask_claude('system prompt', 'content', self.SCHEMA)

    def test_request_shape(self):
        fake = self.claude({'x': 1})
        self.assertEqual(self.ask(), {'x': 1})
        params = fake.requests[0]
        self.assertEqual(params['model'], 'claude-opus-5-5')
        self.assertEqual(params['system'], 'system prompt')
        self.assertEqual(params['output_config'], {'effort': 'low', 'format': {'type': 'json_schema', 'schema': self.SCHEMA}})
        self.assertEqual(params['fallbacks'], 'default')
        self.assertEqual(params['betas'], ['server-side-fallback-2026-07-01'])
        self.assertNotIn('thinking', params)

    def test_models_without_fallbacks_do_not_send_them(self):
        self.set(self.ai.AI_MODEL, 'claude-haiku-5-5')
        fake = self.claude({'x': 1})
        self.ask()
        self.assertEqual(fake.requests[0]['model'], 'claude-haiku-5-5')
        self.assertNotIn('fallbacks', fake.requests[0])

    def test_failures_give_no_answer(self):
        self.claude({'x': 1}, stop_reason='refusal')
        self.assertIsNone(self.ask())
        request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
        self.claude(error=anthropic.APIConnectionError(request=request))
        self.assertIsNone(self.ask())

    def test_not_configured(self):
        self.set(self.ai.ANTHROPIC_API_KEY, '')
        fake = self.claude({'x': 1})
        with mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''}):
            self.assertIsNone(self.ask())
        self.assertEqual(fake.requests, [])


class DuplicateDetectionTest(AITestCase):
    TITLE = 'How can I export my survey data to a CSV file?'

    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice')
        self.original = self.question('Exporting collected survey data as CSV', 'export,csv')
        self.other = self.question('Survey design for forest plots', 'survey-design')
        self.unrelated = self.question('Install problems on Windows', 'install')

    def question(self, title, tags):
        question = Question(author=self.alice, title=title, tagnames=tags, body='A question body that is long enough.')
        question.save()
        return question

    def check(self, title=TITLE, **data):
        response = self.client.post(reverse('ai_duplicate_check'), dict(title=title, **data))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)['duplicates']

    def test_claude_picks_among_keyword_candidates(self):
        fake = self.claude({'duplicates': [
            {'id': self.original.id, 'reason': 'Both ask how to export data to CSV.'},
            {'id': 99999, 'reason': 'Not a candidate: ignored.'},
        ]})
        duplicates = self.check(text='I want <b>CSV</b>')

        self.assertEqual([d['id'] for d in duplicates], [self.original.id])
        self.assertEqual(duplicates[0]['url'], self.original.get_absolute_url())
        self.assertEqual(duplicates[0]['reason'], 'Both ask how to export data to CSV.')
        prompt = fake.prompt
        self.assertIn('<question id="%d">' % self.original.id, prompt)
        self.assertIn('<question id="%d">' % self.other.id, prompt)
        self.assertNotIn('Install problems', prompt)
        self.assertIn('&lt;b&gt;CSV&lt;/b&gt;', prompt)

    def test_results_are_cached(self):
        fake = self.claude({'duplicates': []})
        self.check()
        self.check()
        self.assertEqual(len(fake.requests), 1)

    def test_rate_limit(self):
        self.set(self.ai.AI_DUPLICATE_CHECKS_PER_HOUR, 1)
        fake = self.claude({'duplicates': [{'id': self.original.id, 'reason': 'same'}]})
        self.assertEqual(len(self.check()), 1)
        self.assertEqual(self.check('How do I export survey results to CSV quickly?'), [])
        self.assertEqual(len(fake.requests), 1)

    def test_no_request_for_short_titles_or_when_disabled(self):
        fake = self.claude({'duplicates': []})
        self.assertEqual(self.check('CSV export'), [])
        self.set(self.ai.AI_DUPLICATE_DETECTION, False)
        self.assertEqual(self.check(), [])
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.client.get(reverse('ai_duplicate_check')).status_code, 405)

    def test_ask_page_enables_the_check(self):
        response = self.client.get(reverse('ask'))
        self.assertContains(response, "var ai_duplicates_url = '%s'" % reverse('ai_duplicate_check'))
        self.assertContains(response, 'id="ask-duplicates"')

    def test_related_questions_fall_back_to_keywords(self):
        response = self.client.post(reverse('related_questions'), {'title': self.TITLE})
        titles = [q['title'] for q in json.loads(response.content)]
        self.assertEqual(titles[0], self.original.title)
        self.assertNotIn(self.unrelated.title, titles)


class ModerationTest(AITestCase):
    SPAM = {'verdict': 'spam', 'confidence': 0.95, 'reason': 'Advertises a casino.'}
    OK = {'verdict': 'ok', 'confidence': 0.9, 'reason': 'A real question.'}

    def setUp(self):
        super().setUp()
        self.newbie = self.create_user('newbie')
        self.mod = self.create_user('mod', is_staff=True)
        self.login(self.newbie)

    def ask(self, title='Best online casino bonus codes this week'):
        response = self.client.post(reverse('ask'), {
            'title': title, 'text': 'Visit my site for the best bonus codes, long enough.', 'tags': 'casino'})
        self.assertEqual(response.status_code, 302)
        return Question.objects.get(title=title)

    def test_spam_is_reported_not_hidden(self):
        fake = self.claude(self.SPAM)
        question = self.ask()

        report = ModerationReport.objects.get(node=question)
        self.assertEqual((report.source, report.verdict, report.confidence_percent), ('ai', 'spam', 95))
        self.assertEqual(report.reason, 'Advertises a casino.')
        self.assertFalse(Question.objects.get(id=question.id).nis.deleted)
        self.assertIn('<title>Best online casino bonus codes this week</title>', fake.prompt)
        self.assertIn('Visit my site', fake.prompt)
        self.assertIn('untrusted user text', fake.requests[0]['system'])

    def test_ok_or_unsure_posts_are_not_reported(self):
        self.claude(self.OK, dict(self.SPAM, confidence=0.5))
        self.ask()
        self.ask('Another post that the model is unsure about')
        self.assertFalse(ModerationReport.objects.exists())

    def test_trusted_authors_are_not_reviewed(self):
        fake = self.claude(self.SPAM)
        self.newbie.reputation = 500
        self.newbie.save()
        self.ask()
        self.login(self.mod)
        self.ask('A question by a staff member about casinos')
        self.assertEqual(fake.requests, [])

    def test_answers_are_reviewed_with_their_question(self):
        question = Question(author=self.mod, title='How do I map plot locations?', tagnames='gis',
                            body='Question body text that is long enough.')
        question.save()
        fake = self.claude(self.SPAM)
        self.client.post(reverse('answer', args=[question.id]), {'text': 'Cheap pills here, long enough text.'})

        answer = Answer.objects.get(parent=question)
        self.assertTrue(ModerationReport.objects.filter(node=answer).exists())
        self.assertIn('<in_reply_to_question>How do I map plot locations?</in_reply_to_question>', fake.prompt)

    def test_edits_update_the_open_report(self):
        self.claude(self.SPAM, dict(self.SPAM, verdict='abusive', reason='Now insulting.'))
        question = self.ask()
        self.client.post(reverse('edit_question', args=[question.id]), {
            'title': question.title, 'text': 'An edited body that is still long enough.', 'tags': 'casino',
            'summary': 'edit', 'revision': '1'})
        reports = ModerationReport.objects.filter(node=question)
        self.assertEqual([(r.verdict, r.reason) for r in reports], [('abusive', 'Now insulting.')])

    def resolve(self, report, resolution):
        return self.client.post(reverse('admin_resolve_moderation_report', args=[report.id]),
                                {'resolution': resolution})

    def test_moderators_resolve_reports(self):
        self.claude(dict(self.SPAM, reason='<script>x()</script> spam'))
        question = self.ask()
        report = ModerationReport.objects.get(node=question)

        self.assertEqual(self.resolve(report, 'delete').status_code, 403)
        self.assertFalse(Question.objects.get(id=question.id).nis.deleted)

        self.login(self.mod)
        page = self.client.get(reverse('admin_flagged_posts'))
        self.assertContains(page, 'Reported by automated checks')
        self.assertContains(page, '&lt;script&gt;x()&lt;/script&gt; spam')
        self.assertNotContains(page, '<script>x()</script>')

        self.assertEqual(self.client.get(reverse('admin_resolve_moderation_report', args=[report.id])).status_code, 405)
        self.assertRedirects(self.resolve(report, 'delete'), reverse('admin_flagged_posts'), fetch_redirect_response=False)
        report.refresh_from_db()
        self.assertEqual((report.resolution, report.resolved_by), ('deleted', self.mod))
        self.assertTrue(Question.objects.get(id=question.id).nis.deleted)
        self.assertNotContains(self.client.get(reverse('admin_flagged_posts')), 'Reported by automated checks')

    def test_dismissing_keeps_the_post(self):
        self.claude(self.SPAM)
        question = self.ask()
        report = ModerationReport.objects.get(node=question)
        self.login(self.mod)
        self.resolve(report, 'dismiss')
        report.refresh_from_db()
        self.assertEqual(report.resolution, 'dismissed')
        self.assertFalse(Question.objects.get(id=question.id).nis.deleted)
