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


SUPPORTED = {'answers_question': True, 'unsupported': [], 'supported': True}


class FakeWriter(FakeGeminiWithEmbeddings):
    """Answers with free text; answers the fact check (a JSON-schema request) with `check`."""

    def __init__(self, text, check=SUPPORTED):
        super().__init__({})
        self.text, self.check = text, check

    def generate_content(self, model, contents, config):
        self.requests.append(SimpleNamespace(model=model, contents=contents, config=config))
        text = json.dumps(self.check) if config.response_json_schema else self.text
        return SimpleNamespace(prompt_feedback=None, text=text,
                               candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)])

    @property
    def writes(self):
        return [r for r in self.requests if not r.config.response_json_schema]

    @property
    def checks(self):
        return [r for r in self.requests if r.config.response_json_schema]


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

    def writer(self, text, check=SUPPORTED):
        fake = FakeWriter(text, check)
        patcher = mock.patch('forum_modules.ai.client.get_client', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class AnswerQuestionTest(AssistantTestCase):
    def answer(self, title='Exporting my survey data as CSV', body='I need <b>CSV</b> files.'):
        from forum_modules.ai.assistant import answer_question
        return answer_question(title, body)

    def test_answer_cites_and_passes_the_check(self):
        fake = self.writer('Open the designer and use the CSV export [1]. See also [9].')
        result = self.answer()
        self.assertFalse(result['insufficient'])
        url = 'http://testserver%s' % self.export.get_absolute_url()
        self.assertIn('href="%s"' % url, str(result['html']))
        self.assertNotIn('[9]', str(result['html']))
        self.assertEqual(result['sources'], [{'url': url, 'title': 'How do I export survey data to CSV?', 'kind': 'forum'}])

        request = fake.writes[-1]
        self.assertIn('[1] forum thread: How do I export survey data to CSV?', request.contents)
        self.assertIn('Use the CSV export in the Collect designer.', request.contents)
        self.assertIn('&lt;b&gt;CSV&lt;/b&gt;', request.contents)
        self.assertIn('using only the numbered sources', request.config.system_instruction)
        self.assertIsNone(request.config.tools)  # no web search

        check = fake.checks[-1]
        self.assertIn('Open the designer and use the CSV export [1].', check.contents)
        self.assertIn('Use the CSV export in the Collect designer.', check.contents)
        self.assertEqual(check.config.thinking_config.thinking_level, types.ThinkingLevel.MEDIUM)

    def test_unsupported_statements_mean_no_answer(self):
        self.writer('Click Tools > Export to CSV (version 4.2) [1].',
                    check={'answers_question': True, 'unsupported': ['version 4.2'], 'supported': False})
        result = self.answer()
        self.assertTrue(result['insufficient'])
        self.assertEqual((result['html'], result['sources']), ('', []))

        self.writer('Use the CSV export [1].', check={'answers_question': False, 'unsupported': [], 'supported': True})
        self.assertTrue(self.answer()['insufficient'])

    def test_answers_without_citations_are_not_shown(self):
        fake = self.writer('Just use the CSV export, it is easy.')
        self.assertTrue(self.answer()['insufficient'])
        self.assertEqual(fake.checks, [])

    def test_no_sources_no_model_call(self):
        fake = self.writer('Use the CSV export [1].')
        self.assertTrue(self.answer('Something with no matching sources at all', '')['insufficient'])
        self.assertEqual(fake.requests, [])

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
        self.assertFalse(data['insufficient'])
        self.assertIn('CSV export', data['html'])
        self.assertEqual(data['sources'][0]['kind'], 'forum')
        self.ask(text='details')
        self.assertEqual(len(fake.writes), 1)  # cached

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

    def test_no_suggestion_when_the_check_fails(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('Reinstall Java 8 and click Repair [1].',
                    check={'answers_question': True, 'unsupported': ['Reinstall Java 8', 'click Repair'], 'supported': False})
        self.assertIn('0 added, 1 without enough information', self.run_command())
        self.assertNotContains(self.client.get(waiting.get_absolute_url()), 'Suggested answer')

    def test_insufficient_information_is_remembered_not_shown(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('INSUFFICIENT: Please add the error message.')
        self.assertIn('1 without enough information', self.run_command())
        self.assertEqual(AISuggestion.objects.get(question=waiting).status, AISuggestion.DECLINED)
        self.assertNotContains(self.client.get(waiting.get_absolute_url()), 'Suggested answer')

    def test_question_page_and_feedback(self):
        waiting = self.question('Exporting survey data to CSV fails')
        self.writer('Use the CSV export [1].')
        self.run_command()
        suggestion = AISuggestion.objects.get(question=waiting)

        page = self.client.get(waiting.get_absolute_url())
        self.assertContains(page, 'Suggested answer')
        self.assertContains(page, 'not by a person')
        self.assertContains(page, 'href="http://testserver%s"' % self.export.get_absolute_url())
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
