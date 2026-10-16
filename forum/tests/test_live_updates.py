"""The live question page: GET /questions/<id>/updates/ (forum/views/live.py,
forum/utils/live.py) and edit conflicts on the edit pages."""
import datetime
import json

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from forum.actions import AcceptAnswerAction, VoteUpAction
from forum.models import Answer, Comment, Node, Question
from forum.utils import live

from .base import OSQATestCase


class LiveTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500, email_isvalid=True)
        self.bob = self.create_user('bob', reputation=500, email_isvalid=True)
        self.carol = self.create_user('carol', reputation=500, email_isvalid=True)
        self.mod = self.create_user('mod', reputation=10, is_staff=True, email_isvalid=True)
        self.question = self.post(Question(author=self.alice, title='How do I read survey dates in R?',
                                           tagnames='collect r', body='The dates come out as text when I read them.'))
        self.answer = self.post(Answer(author=self.bob, parent=self.question,
                                       body='Use colClasses to read the column as a date.'))
        self.comment = self.post(Comment(author=self.carol, parent=self.question, body='Which version do you use?'))
        self.url = reverse('question_updates', kwargs={'id': self.question.id})
        self.since = live.now_token()

    def post(self, node, minutes_ago=60):
        node.save()
        Node.objects.filter(id=node.id).update(added_at=datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago))
        node.uncache()
        return type(node).objects.get(id=node.id)

    def set_state(self, node, state):
        Node.objects.filter(id=node.id).update(state_string=state)
        Node.objects.get(id=node.id).uncache()
        Node._set_query_cache_invalidation_timestamp()

    def poll(self, user=None, since=None, etag=None, **params):
        self.client.logout()
        if user is not None:
            self.login(user)
        params.setdefault('since', since or self.since)
        extra = {'HTTP_IF_NONE_MATCH': etag} if etag else {}
        return self.client.get(self.url, params, **extra)

    def snapshot(self, user=None, **kwargs):
        response = self.poll(user, **kwargs)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)


class SnapshotTest(LiveTestCase):
    def test_lists_what_the_page_shows(self):
        data = self.snapshot()
        self.assertEqual(data['question'], {'id': self.question.id, 'rev': self.question.active_revision_id,
                                            'score': 0})
        self.assertEqual(data['answers'], [{'id': self.answer.id, 'rev': self.answer.active_revision_id,
                                            'score': 0, 'accepted': False, 'new': False}])
        self.assertEqual(data['comments'], [{'id': self.comment.id, 'parent': self.question.id,
                                             'rev': self.comment.active_revision_id, 'score': 0}])
        self.assertEqual(data['poll'], live.POLL_SECONDS)

    def test_since_token_marks_new_answers(self):
        newer = Answer(author=self.carol, parent=self.question, body='Or lubridate: ymd(data$survey_date).')
        newer.save()
        answers = {a['id']: a['new'] for a in self.snapshot()['answers']}
        self.assertEqual(answers, {self.answer.id: False, newer.id: True})
        # A later token: nothing is new any more.
        later = str(int(self.since) + 3600 * 1000)
        self.assertEqual({a['id']: a['new'] for a in self.snapshot(since=later)['answers']},
                         {self.answer.id: False, newer.id: False})

    def test_votes_accepts_and_edits_change_the_snapshot(self):
        VoteUpAction(user=self.carol, node=self.answer, ip='127.0.0.1').save()
        AcceptAnswerAction(user=self.alice, node=Answer.objects.get(id=self.answer.id), ip='127.0.0.1').save()
        answer = self.snapshot()['answers'][0]
        self.assertEqual((answer['score'], answer['accepted']), (1, True))

        self.login(self.bob)
        self.client.post(reverse('edit_answer', args=[self.answer.id]), {
            'text': 'Use colClasses, or as.Date() after reading the file.', 'summary': 'more', 'revision': '1',
            'base_revision': '1'})
        self.assertNotEqual(self.snapshot()['answers'][0]['rev'], self.answer.active_revision_id)

    def test_bad_requests(self):
        self.assertEqual(self.poll(since='yesterday').status_code, 400)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        missing = reverse('question_updates', kwargs={'id': 999999})
        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_no_store_in_shared_caches(self):
        response = self.poll()
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertIn('Cookie', response['Vary'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex')


class VisibilityTest(LiveTestCase):
    def ids(self, data, kind):
        return {item['id'] for item in data[kind]}

    def test_held_posts_only_for_their_author_and_moderators(self):
        held = self.post(Answer(author=self.carol, parent=self.question, body='A held answer from a newcomer.'))
        under = self.post(Comment(author=self.alice, parent=held, body='A comment under the held answer.'))
        self.set_state(held, '(pending_review)')
        for user, sees in ((None, False), (self.bob, False), (self.carol, True), (self.mod, True)):
            data = self.snapshot(user)
            self.assertEqual(held.id in self.ids(data, 'answers'), sees, user)
            self.assertEqual(under.id in self.ids(data, 'comments'), sees, user)
            rendered = json.loads(self.poll(user, posts='%d,%d' % (held.id, under.id)).content)
            self.assertEqual(bool(rendered['posts']), sees, user)

    def test_deleted_posts_for_nobody(self):
        self.set_state(self.answer, '(deleted)')
        self.set_state(self.comment, '(deleted)')
        for user in (None, self.bob, self.mod):
            data = self.snapshot(user)
            self.assertEqual((data['answers'], data['comments']), ([], []))
            rendered = json.loads(self.poll(user, posts='%d,%d' % (self.answer.id, self.comment.id)).content)
            self.assertEqual(rendered['posts'], [])

    def test_hidden_question_is_404(self):
        self.set_state(self.question, '(pending_review)')
        self.assertEqual(self.poll().status_code, 404)
        self.assertEqual(self.poll(self.bob).status_code, 404)
        self.assertEqual(self.poll(self.alice).status_code, 200)
        self.set_state(self.question, '(deleted)')
        self.assertEqual(self.poll(self.bob).status_code, 404)

    def test_posts_of_other_questions_are_ignored(self):
        other = self.post(Question(author=self.bob, title='Another question about maps', tagnames='maps',
                                   body='Some other question body text.'))
        foreign = self.post(Answer(author=self.carol, parent=other, body='An answer to the other question.'))
        rendered = json.loads(self.poll(posts='%d,%d' % (foreign.id, self.answer.id)).content)
        self.assertEqual([p['id'] for p in rendered['posts']], [self.answer.id])


class RenderTest(LiveTestCase):
    def test_renders_posts_with_the_page_templates(self):
        reply = self.post(Comment(author=self.alice, parent=self.answer, body='Thanks, colClasses did it.'))
        self.login(self.carol)
        rendered = json.loads(self.client.get(self.url, {'posts': '%d,%d,%d' % (
            self.answer.id, reply.id, self.question.id)}).content)
        answer, comment = rendered['posts']
        self.assertEqual((answer['type'], comment['type'], comment['parent']), ('answer', 'comment', self.answer.id))
        self.assertIn('id="answer-container-%d"' % self.answer.id, answer['html'])
        self.assertIn('data-rev="%d"' % self.answer.active_revision_id, answer['html'])
        self.assertIn('Use colClasses', answer['html'])
        self.assertIn('id="post-%d-upvote"' % self.answer.id, answer['html'])  # carol may vote
        self.assertIn('id="comment-%d"' % reply.id, comment['html'])
        self.assertIn('Thanks, colClasses did it.', comment['html'])
        # Only the comment form's inert template: nothing that would run.
        self.assertEqual(answer['html'].count('<script'), answer['html'].count('<script type="text/html"'))
        question = rendered['question']
        self.assertEqual(question['title'], self.question.headline)
        self.assertIn('The dates come out as text', question['body'])
        self.assertIn('rel="tag"', question['tags'])
        self.assertIn('post-update-info', question['info'])
        self.assertEqual(rendered['answers_heading'], '1 Answer')

    def test_question_page_carries_the_live_script_except_for_crawlers(self):
        page = self.client.get(self.question.get_absolute_url()).content.decode()
        self.assertIn('data-url="%s"' % self.url, page)
        self.assertIn('osqa.live.js', page)
        self.assertIn('data-rev="%d"' % self.question.active_revision_id, page)
        self.assertIn('data-rev="%d"' % self.answer.active_revision_id, page)
        self.assertIn('data-rev="%d"' % self.comment.active_revision_id, page)
        bot = self.client.get(self.question.get_absolute_url(),
                              HTTP_USER_AGENT='Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)')
        self.assertNotIn('osqa.live.js', bot.content.decode())
        self.assertIn('id="live-answers"', bot.content.decode())


class ConditionalTest(LiveTestCase):
    def count(self, user, etag):
        # Right after self.poll(user): the browser's session is already there.
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url, {'since': self.since}, HTTP_IF_NONE_MATCH=etag)
        return response, len(ctx.captured_queries), ctx

    def test_unchanged_question_is_304_without_queries_of_its_own(self):
        for user in (None, self.bob):
            etag = self.poll(user)['ETag']
            response, queries, ctx = self.count(user, etag)
            self.assertEqual(response.status_code, 304)
            self.assertEqual(response['ETag'], etag)
            # The session (and the signed-in user): the middleware's, none of its own.
            self.assertLessEqual(queries, 2 if user else 1, '\n'.join(q['sql'] for q in ctx.captured_queries))
            self.assertFalse([q for q in ctx.captured_queries if 'forum_node' in q['sql']])

    def test_change_elsewhere_is_304_with_one_query(self):
        etag = self.poll()['ETag']
        self.post(Question(author=self.bob, title='Another question about maps', tagnames='maps',
                           body='Some other question body text.'))
        response, queries, ctx = self.count(None, etag)
        self.assertEqual(response.status_code, 304)
        self.assertNotEqual(response['ETag'], etag)
        self.assertEqual(live.split_etag(response['ETag'])[1], live.split_etag(etag)[1])
        # The session, the question (object cache) and the snapshot.
        self.assertLessEqual(queries, 3, '\n'.join(q['sql'] for q in ctx.captured_queries))

    def test_change_here_is_200(self):
        etag = self.poll()['ETag']
        VoteUpAction(user=self.carol, node=self.answer, ip='127.0.0.1').save()
        response = self.poll(etag=etag)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['answers'][0]['score'], 1)

    def test_etag_is_per_viewer_and_token(self):
        etag = self.poll()['ETag']
        # Another viewer or token: the snapshot is compared again (same content: 304, new ETag).
        self.assertNotEqual(self.poll(self.bob, etag=etag)['ETag'], etag)
        self.assertNotEqual(self.poll(since=str(int(self.since) + 1), etag=etag)['ETag'], etag)
        # The author sees her held answer: not the anonymous snapshot.
        held = self.post(Answer(author=self.bob, parent=self.question, body='A held answer from bob here.'))
        self.set_state(held, '(pending_review)')
        etag = self.poll()['ETag']
        self.assertEqual(self.poll(self.bob, etag=etag).status_code, 200)

    def test_without_cache_stamps_the_snapshot_decides(self):
        etag = self.poll()['ETag']
        cache.clear()
        response = self.poll(etag=etag)
        self.assertEqual(response.status_code, 304)


class EditConflictTest(LiveTestCase):
    def edit_question(self, base, text, **extra):
        self.login(self.alice)
        data = {'title': self.question.title, 'tags': 'collect r', 'summary': 'edit', 'revision': '1', 'text': text}
        if base is not None:
            data['base_revision'] = str(base)
        data.update(extra)
        return self.client.post(reverse('edit_question', args=[self.question.id]), data)

    def body(self, node):
        return Node.objects.filter(id=node.id).values_list('body', flat=True)[0]

    def test_edit_page_carries_the_revision(self):
        self.login(self.alice)
        page = self.client.get(reverse('edit_question', args=[self.question.id])).content.decode()
        self.assertIn('name="base_revision" value="1"', page)

    def test_saving_over_a_newer_revision_keeps_the_text_and_explains(self):
        self.assertEqual(self.edit_question(1, 'First tab: the dates are read as text in R.').status_code, 302)
        response = self.edit_question(1, 'Second tab: my own wording of the question.')
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('This post was changed while you were editing it', page)
        self.assertIn('alice saved revision 2 after you opened this page', page)
        self.assertIn('Second tab: my own wording of the question.', page)
        self.assertIn(reverse('revisions', kwargs={'id': self.question.id}), page)
        self.assertIn('name="base_revision" value="2"', page)
        self.assertIn('First tab', self.body(self.question))
        # Saving again (the form now starts from revision 2) replaces it.
        self.assertEqual(self.edit_question(2, 'Second tab: my own wording of the question.').status_code, 302)
        self.assertIn('Second tab', self.body(self.question))

    def test_no_base_revision_and_picking_a_revision_are_not_conflicts(self):
        self.assertEqual(self.edit_question(1, 'First tab: the dates are read as text in R.').status_code, 302)
        response = self.edit_question(1, 'Anything', select_revision='select revision')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('This post was changed', response.content.decode())
        self.assertIn('name="base_revision" value="1"', response.content.decode())
        self.assertEqual(self.edit_question(None, 'An old form without the field saves.').status_code, 302)

    def test_answer_edit_conflict(self):
        self.login(self.bob)
        url = reverse('edit_answer', args=[self.answer.id])
        data = {'summary': 'edit', 'revision': '1', 'base_revision': '1'}
        self.assertEqual(self.client.post(url, dict(data, text='Read the column with colClasses = "Date".')).status_code, 302)
        response = self.client.post(url, dict(data, text='My other version of this answer text.'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('This post was changed while you were editing it', response.content.decode())
        self.assertIn('My other version of this answer text.', response.content.decode())
        self.assertIn('colClasses = "Date"', self.body(self.answer))
