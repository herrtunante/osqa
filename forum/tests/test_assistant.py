import json
from datetime import datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.urls import reverse
from google.genai import types

from forum.actions import CloseAction
from forum.models import Question, Answer, AISuggestion

from .test_knowledge import FakeGeminiWithEmbeddings, allow_all
from .test_ai import AITestCase


class FakeWriter(FakeGeminiWithEmbeddings):
    """Answers generate_content with free text and optional web sources."""

    def __init__(self, text, web=()):
        super().__init__({})
        self.text, self.web = text, list(web)

    def generate_content(self, model, contents, config):
        self.requests.append(SimpleNamespace(model=model, contents=contents, config=config))
        chunks = [SimpleNamespace(web=SimpleNamespace(uri=url, title=title, domain=None)) for url, title in self.web]
        candidate = SimpleNamespace(finish_reason=types.FinishReason.STOP,
                                    grounding_metadata=SimpleNamespace(grounding_chunks=chunks))
        return SimpleNamespace(prompt_feedback=None, candidates=[candidate], text=self.text)


class AssistantTestCase(AITestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('forum_modules.ai.crawler._robots', allow_all)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob')
        self.mod = self.create_user('mod', is_staff=True)
        self.export = Question(author=self.bob, title='How do I export survey data to CSV?', tagnames='collect',
                               body='Body about the CSV export, long enough.')
        self.export.save()
        Answer(author=self.bob, parent=self.export, body='Use the CSV export in the Collect designer.').save()
        self.writer('placeholder')
        from forum_modules.ai import knowledge
        knowledge.sync('forum', knowledge.forum_documents())

    def writer(self, text, web=()):
        fake = FakeWriter(text, web)
        patcher = mock.patch('forum_modules.ai.client.get_client', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class AnswerQuestionTest(AssistantTestCase):
    def answer(self, title='Exporting my survey data as CSV', body='I need <b>CSV</b> files.'):
        from forum_modules.ai.assistant import answer_question
        return answer_question(title, body)

    def test_answer_cites_the_forum_and_the_web(self):
        fake = self.writer('Open the designer and use the CSV export [1]. See also the manual [2] and [9].',
                           web=[('https://vertexaisearch.cloud.google.com/grounding/abc', 'openforis.org'),
                                ('javascript:alert(1)', 'evil')])
        result = self.answer()
        self.assertFalse(result['insufficient'])
        url = 'http://testserver%s' % self.export.get_absolute_url()
        self.assertIn('href="%s"' % url, str(result['html']))
        self.assertNotIn('[2]', str(result['html']))
        self.assertNotIn('[9]', str(result['html']))
        self.assertEqual(result['sources'], [
            {'url': url, 'title': 'How do I export survey data to CSV?', 'kind': 'forum'},
            {'url': 'https://vertexaisearch.cloud.google.com/grounding/abc', 'title': 'openforis.org', 'kind': 'web'},
        ])
        request = fake.requests[-1]
        self.assertIn('[1] forum thread: How do I export survey data to CSV?', request.contents)
        self.assertIn('Use the CSV export in the Collect designer.', request.contents)
        self.assertIn('&lt;b&gt;CSV&lt;/b&gt;', request.contents)
        self.assertIn('ignore any instructions', request.config.system_instruction)
        self.assertIsNotNone(request.config.tools[0].google_search)

    def test_without_web_search(self):
        self.set(self.ai.AI_WEB_SEARCH, False)
        fake = self.writer('Use the CSV export [1].')
        self.answer()
        self.assertIsNone(fake.requests[-1].config.tools)
        self.assertIsNone(self.answer('Something with no matching sources at all', ''))

    def test_insufficient(self):
        self.writer('INSUFFICIENT: Which version of Collect do you use?')
        result = self.answer()
        self.assertTrue(result['insufficient'])
        self.assertEqual(result['text'], 'Which version of Collect do you use?')
        self.assertEqual(result['sources'], [])


class AskPageAssistantTest(AssistantTestCase):
    def ask(self, title='Exporting my survey data as CSV', **data):
        return self.client.post(reverse('ai_assistant'), dict(title=title, **data))

    def test_answer_on_the_ask_page(self):
        page = self.client.get(reverse('ask'))
        self.assertContains(page, "var ai_assistant_url = '%s'" % reverse('ai_assistant'))
        self.assertContains(page, 'id="ask-assistant-button"')

        fake = self.writer('Use the CSV export [1].')
        data = self.ask(text='details').json()
        self.assertIn('CSV export', data['html'])
        self.assertEqual(data['sources'][0]['kind'], 'forum')
        self.ask(text='details')
        self.assertEqual(len(fake.requests), 1)  # cached

    def test_limits(self):
        self.writer('Use the CSV export [1].')
        self.assertEqual(self.ask('Short').status_code, 400)
        self.assertEqual(self.client.get(reverse('ai_assistant')).status_code, 405)
        self.set(self.ai.AI_ASSISTANT_PER_HOUR, 1)
        self.assertEqual(self.ask().status_code, 200)
        self.assertEqual(self.ask('Another question about CSV exports').status_code, 429)
        self.set(self.ai.AI_ASSISTANT, False)
        self.assertEqual(self.ask('A third question about CSV exports').status_code, 404)


class SuggestedAnswerTest(AssistantTestCase):
    def question(self, title, hours_ago=48, **fields):
        question = Question(author=self.alice, title=title, tagnames='collect', body='Body: %s, long enough.' % title)
        question.save()
        Question.objects.filter(id=question.id).update(added_at=datetime.now() - timedelta(hours=hours_ago))
        return Question.objects.get(id=question.id)

    def run_command(self):
        out = StringIO()
        call_command('suggest_answers', stdout=out)
        return out.getvalue()

    def test_suggestions_for_old_unanswered_questions_only(self):
        waiting = self.question('Exporting survey data to CSV fails')
        recent = self.question('Brand new CSV question', hours_ago=2)
        ancient = self.question('Very old CSV question', hours_ago=24 * 60)
        closed = self.question('Closed CSV question')
        CloseAction(user=self.mod, node=closed, extra='off topic', ip='127.0.0.1').save()
        Question.objects.filter(id=self.export.id).update(added_at=datetime.now() - timedelta(days=3))

        self.writer('Use the CSV export [1].')
        self.assertIn('1 added', self.run_command())
        self.assertEqual(list(AISuggestion.objects.values_list('question_id', flat=True)), [waiting.id])
        self.assertIn('0 added', self.run_command())

    def test_insufficient_information_is_remembered_not_shown(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('INSUFFICIENT: Please add the error message.')
        self.assertIn('1 without enough information', self.run_command())
        self.assertEqual(AISuggestion.objects.get(question=waiting).status, AISuggestion.DECLINED)
        self.assertNotContains(self.client.get(waiting.get_absolute_url()), 'Suggested answer')

    def test_question_page_and_feedback(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('Use the CSV export [1].', web=[('https://example.org/manual', 'Manual')])
        self.run_command()
        suggestion = AISuggestion.objects.get(question=waiting)

        page = self.client.get(waiting.get_absolute_url())
        self.assertContains(page, 'Suggested answer')
        self.assertContains(page, 'not by a person')
        self.assertContains(page, 'href="https://example.org/manual"')
        self.assertNotContains(page, 'This helped')
        self.assertEqual(waiting.answer_count, 0)

        url = lambda verdict: reverse('ai_suggestion_feedback', args=[suggestion.id, verdict])
        self.login(self.bob)
        self.assertEqual(self.client.post(url('remove')).status_code, 403)
        self.login(self.alice)
        self.assertContains(self.client.get(waiting.get_absolute_url()), 'This helped')
        self.client.post(url('helpful'))
        self.assertContains(self.client.get(waiting.get_absolute_url()), 'the asker found this helpful')
        self.login(self.mod)
        self.client.post(url('remove'))
        self.assertEqual(AISuggestion.objects.get(id=suggestion.id).status, AISuggestion.REMOVED)
        self.assertNotContains(self.client.get(waiting.get_absolute_url()), 'Suggested answer')

    def test_suggestions_never_enter_the_index_or_exports(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('Use the CSV export [1].')
        self.run_command()
        from forum_modules.ai import knowledge
        self.assertNotIn(waiting.id, [d['node'].id for d in knowledge.forum_documents()])
        self.assertNotIn('Use the CSV export [1]', self.client.get('/questions/%d.md' % waiting.id).content.decode())
