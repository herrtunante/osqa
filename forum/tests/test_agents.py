import json


from forum.actions import AcceptAnswerAction, DeleteAction
from forum.models import Question, Answer, Comment

from .base import OSQATestCase


class ForumFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice')
        self.bob = self.create_user('bob')
        self.question = Question(author=self.alice, title='How do I export Collect data to CSV?', tagnames='collect export',
                                 body='I need the **CSV** export of my survey.')
        self.question.save()
        self.plain = Answer(author=self.bob, parent=self.question, body='Maybe try the backup file first, long enough.')
        self.plain.save()
        self.accepted = Answer(author=self.bob, parent=self.question, body='Use *Data export* > CSV in the designer.')
        self.accepted.save()
        AcceptAnswerAction(user=self.alice, node=self.accepted, ip='127.0.0.1').save()
        self.removed = Answer(author=self.bob, parent=self.question, body='Deleted answer text that is long enough.')
        self.removed.save()
        DeleteAction(user=self.bob, node=self.removed, ip='127.0.0.1').save()
        Comment(author=self.alice, parent=self.accepted, body='Thanks, that worked for version 4.').save()
        self.unanswered = Question(author=self.alice, title='Unanswered question about Earth Map', tagnames='earthmap',
                                   body='Nobody has answered this question yet.')
        self.unanswered.save()


class AgentAccessTest(ForumFixture):
    def test_question_markdown(self):
        response = self.client.get('/questions/%d.md' % self.question.id)
        self.assertEqual(response['Content-Type'], 'text/markdown; charset=utf-8')
        text = response.content.decode()
        self.assertTrue(text.startswith('---\ntitle: "How do I export Collect data to CSV?"\nurl: "http'))
        self.assertIn('tags: ["collect", "export"]', text)
        self.assertIn('status: "accepted"', text)
        self.assertIn('I need the **CSV** export of my survey.', text)
        self.assertLess(text.index('## Accepted answer'), text.index('Maybe try the backup'))
        self.assertIn('Use *Data export* > CSV in the designer.', text)
        self.assertIn('> Thanks, that worked for version 4. (alice)', text)
        self.assertNotIn('Deleted answer text', text)
        self.assertIn('rel="canonical"', response['Link'])

    def test_question_page_links_its_markdown(self):
        page = self.client.get(self.question.get_absolute_url())
        self.assertContains(page, 'type="text/markdown" title="Markdown" href="/questions/%d.md"' % self.question.id)

    def test_deleted_questions_are_not_exposed(self):
        DeleteAction(user=self.alice, node=self.question, ip='127.0.0.1').save()
        self.assertEqual(self.client.get('/questions/%d.md' % self.question.id).status_code, 404)

    def test_llms_txt(self):
        text = self.client.get('/llms.txt').content.decode()
        self.assertTrue(text.startswith('# '))
        self.assertIn('/export/qa.jsonl', text)
        self.assertIn('/mcp', text)
        self.assertIn('[How do I export Collect data to CSV?](http://testserver/questions/%d.md)' % self.question.id, text)
        self.assertNotIn('Unanswered question', text)

    def test_jsonl_export(self):
        response = self.client.get('/export/qa.jsonl')
        rows = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual([row['id'] for row in rows], [self.question.id])
        row = rows[0]
        self.assertEqual(row['status'], 'accepted')
        self.assertEqual([a['accepted'] for a in row['answers']], [True, False])
        self.assertEqual(row['question'], 'I need the **CSV** export of my survey.')


class MCPTest(ForumFixture):
    def rpc(self, method, params=None, id=1, raw=None):
        body = raw if raw is not None else json.dumps({'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params or {}})
        return self.client.post('/mcp', body, content_type='application/json', HTTP_ACCEPT='application/json, text/event-stream')

    def test_initialize_and_list_tools(self):
        result = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                         'clientInfo': {'name': 'test', 'version': '1'}}).json()['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertIn('tools', result['capabilities'])
        self.assertEqual(self.rpc('initialize', {'protocolVersion': '1999-01-01'}).json()['result']['protocolVersion'],
                         '2025-06-18')
        tools = self.rpc('tools/list').json()['result']['tools']
        self.assertEqual([t['name'] for t in tools], ['search_forum', 'get_question'])
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in tools))

    def test_search_and_read(self):
        result = self.rpc('tools/call', {'name': 'search_forum', 'arguments': {'query': 'export CSV from Collect'}}).json()['result']
        self.assertFalse(result['isError'])
        hits = json.loads(result['content'][0]['text'])['results']
        self.assertEqual(hits[0]['id'], self.question.id)
        self.assertEqual(hits[0]['status'], 'accepted')

        result = self.rpc('tools/call', {'name': 'get_question', 'arguments': {'id': self.question.id}}).json()['result']
        self.assertIn('## Accepted answer', result['content'][0]['text'])

    def test_errors(self):
        result = self.rpc('tools/call', {'name': 'get_question', 'arguments': {'id': 99999}}).json()['result']
        self.assertTrue(result['isError'])
        self.assertEqual(self.rpc('no/such/method').json()['error']['code'], -32601)
        self.assertEqual(self.rpc(None, raw='{not json').status_code, 400)
        notification = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(self.rpc(None, raw=notification).status_code, 202)
        self.assertEqual(self.client.get('/mcp').status_code, 405)

    def test_batches(self):
        batch = json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}])
        self.assertEqual([r['id'] for r in self.rpc(None, raw=batch).json()], [1, 2])
