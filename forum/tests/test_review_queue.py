"""First posts review queue (FIRST_POSTS_MODERATION, forum/utils/review.py)
and the moderation inbox (forum/views/moderation.py).

A held post (mode "before") must be invisible to anonymous visitors and other
members everywhere they could see it, and visible to its author and the
moderators; approving it publishes it and sends the notifications that were
held back."""
import json

from django.urls import reverse

from forum import settings
from forum.actions import ApprovePostAction, FlagAction, ReportAction
from forum.models import (Action, Answer, Comment, MarkedTag, ModerationReport, Node, Notification, Question,
                          QuestionSubscription, SearchDocument, Tag, User)
from forum.utils import review
from forum.utils.similar import candidate_questions

from .base import OSQATestCase

TITLE = 'Where can I download the zanzibar mapping toolkit?'
ANSWER = 'Use the quokka exporter from the tools menu, it works well.'
COMMENT = 'Have you tried the wombat converter for this?'


def fresh(node):
    return Node.objects.get(id=node.id)


class ReviewTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.mod = self.create_user('mod', is_staff=True, email_isvalid=True, reputation=10)
        self.alice = self.create_user('alice', email_isvalid=True, reputation=100)
        self.bob = self.create_user('bob', email_isvalid=True, reputation=100)
        # Enough reputation to comment on others' posts (REP_TO_COMMENT), no posts yet.
        self.newbie = self.create_user('newbie', email_isvalid=True, reputation=60)
        self.question = Question(author=self.alice, title='How do I export survey data to CSV files?',
                                 tagnames='collect', body='A question body that is long enough.')
        self.question.save()
        QuestionSubscription(user=self.alice, question=self.question).save()
        self.set(settings.FIRST_POSTS_MODERATION, 'before')

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def ask(self, user=None, title=TITLE, tags='collect zanzibar'):
        self.login(user or self.newbie)
        response = self.client.post(reverse('ask'), {
            'title': title, 'text': 'I need the zanzibar toolkit for a field survey, where is it?', 'tags': tags})
        self.assertEqual(response.status_code, 302)
        return Question.objects.get(title=title)

    def answer(self, user=None, question=None, text=ANSWER):
        self.login(user or self.newbie)
        question = question or self.question
        self.client.post(reverse('answer', args=[question.id]), {'text': text})
        return Answer.objects.filter(parent=question).latest('id')

    def comment(self, post, user=None, text=COMMENT):
        self.login(user or self.newbie)
        result = self.command(reverse('comment', args=[post.id]), {'comment': text})
        self.assertTrue(result['success'], result)
        return Comment.objects.filter(parent=post).latest('id'), result

    def approve(self, node, moderator=None):
        return ApprovePostAction(user=moderator or self.mod, node=fresh(node), ip='127.0.0.1').save()

    def as_viewers(self):
        """(viewer, may see held posts) for anonymous, another member, the author and a moderator."""
        return ((None, False), (self.bob, False), (self.newbie, True), (self.mod, True))

    def get_as(self, user, url, **kwargs):
        self.client.logout()
        if user is not None:
            self.login(user)
        return self.client.get(url, **kwargs)


class StateTest(ReviewTestCase):
    def test_off_publishes_everything(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'off')
        question = self.ask()
        self.assertEqual(question.state_string, '')
        self.assertFalse(Action.objects.filter(action_type='queueforreview').exists())

    def test_before_holds_a_newcomers_first_post_only(self):
        question = self.ask()
        self.assertTrue(question.nis.pending_review)
        self.assertEqual(question.nstate.pending_review.action_type, 'queueforreview')
        self.assertFalse(question.is_notifiable)
        # A second post before the first is approved is held too ...
        answer = self.answer()
        self.assertTrue(answer.nis.pending_review)
        self.approve(question)
        self.approve(answer)
        self.assertFalse(fresh(question).nis.pending_review)
        # ... and once one is approved, the next is published (FIRST_POSTS_REVIEW_COUNT = 1).
        comment, result = self.comment(self.question)
        self.assertEqual(comment.state_string, '')
        self.assertNotIn('message', result)

    def test_review_count_and_reputation(self):
        self.set(settings.FIRST_POSTS_REVIEW_COUNT, 2)
        first = self.answer(text='A first answer that is long enough to post.')
        self.approve(first)
        self.assertTrue(self.answer(text='A second answer that is long enough to post.').nis.pending_review)
        self.set(settings.FIRST_POSTS_REVIEW_COUNT, 0)
        self.assertFalse(review.needs_review(User.objects.get(id=self.newbie.id)))
        self.set(settings.FIRST_POSTS_REVIEW_MIN_REP, 80)
        self.assertTrue(review.needs_review(User.objects.get(id=self.newbie.id)))  # 60 reputation
        self.assertFalse(review.needs_review(self.alice))  # 100 reputation

    def test_moderators_and_members_with_posts_are_not_reviewed(self):
        self.assertFalse(self.answer(self.mod, text='A moderator answer that is long enough.').state_string)
        self.assertFalse(self.answer(self.alice, text='An answer by a member with posts, long enough.').state_string)

    def test_after_publishes_and_queues(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'after')
        question = self.ask()
        self.assertTrue(question.nis.unreviewed)
        self.assertFalse(question.nis.pending_review)
        self.assertEqual(self.get_as(None, question.get_absolute_url()).status_code, 200)
        self.assertEqual(list(review.review_queue()), [question])
        self.approve(question)
        self.assertEqual(fresh(question).state_string, '')
        self.assertEqual(list(review.review_queue()), [])

    def test_replies_under_a_held_post_are_held_and_approved_with_it(self):
        question = self.ask()
        comment, result = self.comment(question)
        self.assertTrue(comment.nis.pending_review)
        self.assertIn('moderator has reviewed', result['message'])
        self.approve(question)
        self.assertFalse(fresh(comment).nis.pending_review)

    def test_author_notice_after_posting(self):
        self.login(self.newbie)
        response = self.client.post(reverse('ask'), {
            'title': TITLE, 'text': 'I need the zanzibar toolkit for a field survey, where is it?',
            'tags': 'collect'}, follow=True)
        self.assertContains(response, 'Your post will appear once a moderator has reviewed it')
        self.assertContains(response, 'Waiting for review')

    def test_undo_approval_holds_again(self):
        question = self.ask()
        approval = self.approve(question)
        self.assertEqual(Tag.objects.get(name='zanzibar').used_count, 1)
        Action.objects.get(id=approval.id).leaf.cancel(self.mod)
        question = fresh(question)
        self.assertTrue(question.nis.pending_review)
        self.assertEqual(Tag.objects.get(name='zanzibar').used_count, 0)
        self.assertEqual(self.get_as(None, question.get_absolute_url()).status_code, 404)


class VisibilityTest(ReviewTestCase):
    """Each place a held question, answer or comment could show up."""

    def setUp(self):
        super().setUp()
        self.held_question = self.ask()
        self.held_answer = self.answer()
        self.held_comment, _ = self.comment(self.question, text=COMMENT)

    def test_daily_digest(self):
        import datetime
        from forum.management.commands.send_email_alerts import DigestQuestionsIndex
        digest = DigestQuestionsIndex(datetime.datetime.now() - datetime.timedelta(days=1))
        self.assertIn(self.question.id, [q.id for q in digest.questions])
        self.assertNotIn(self.held_question.id, [q.id for q in digest.questions])

    def test_question_page_answers_and_comments(self):
        for user, sees in self.as_viewers():
            with self.subTest(user=user):
                response = self.get_as(user, self.held_question.get_absolute_url())
                self.assertEqual(response.status_code, 200 if sees else 404)
                page = self.get_as(user, self.question.get_absolute_url()).content.decode()
                self.assertEqual(ANSWER in page, sees)
                self.assertEqual(COMMENT in page, sees)
                self.assertEqual('Waiting for review' in page, sees)

    def test_permanent_links_revisions_edit_and_markdown(self):
        for user, sees in self.as_viewers():
            with self.subTest(user=user):
                ok = 200 if sees else 404
                self.assertEqual(self.get_as(user, reverse('revisions', args=[self.held_answer.id])).status_code, ok)
                self.assertEqual(self.get_as(user, self.held_answer.get_absolute_url()).status_code,
                                 302 if sees else 404)
                if user is not None:
                    self.assertEqual(self.get_as(user, reverse('edit_answer', args=[self.held_answer.id]))
                                     .status_code in (200, 302), sees)
        self.login(self.bob)
        self.assertEqual(self.client.post(reverse('comment', args=[self.held_answer.id]), {
            'comment': 'Trying to comment on a held answer.'}, **self.AJAX).status_code, 404)
        self.assertEqual(self.client.post(reverse('vote_post', args=[self.held_answer.id, 'up']),
                                          **self.AJAX).status_code, 404)

    def test_question_lists(self):
        for url in ('/', '/questions/', '/questions/unanswered/', '/tags/collect/'):
            for user, sees in self.as_viewers():
                with self.subTest(url=url, user=user):
                    self.assertEqual(TITLE in self.get_as(user, url).content.decode(), sees)
        # A tag only the held question uses isn't counted, so it has no page yet.
        self.assertEqual(self.get_as(None, '/tags/zanzibar/').status_code, 404)

    def test_answer_counts_and_tag_counts(self):
        self.assertEqual(Question.objects.get(id=self.question.id).answer_count, 0)
        self.assertEqual(Tag.objects.get(name='zanzibar').used_count, 0)
        self.assertEqual(Tag.objects.get(name='collect').used_count, 1)  # the setUp question's only
        self.approve(self.held_question)
        self.approve(self.held_answer)
        self.assertEqual(Question.objects.get(id=self.question.id).answer_count, 1)
        self.assertEqual(Tag.objects.get(name='zanzibar').used_count, 1)

    def test_search_index_and_results(self):
        self.assertFalse(SearchDocument.objects.filter(question_id=self.held_question.id).exists())
        self.assertNotIn('quokka', SearchDocument.objects.get(question_id=self.question.id).answers)
        for user, _sees in self.as_viewers():
            with self.subTest(user=user):
                # Search results mark the words in titles.
                self.assertNotContains(self.get_as(user, '/search/?q=zanzibar'), '<mark>zanzibar</mark> mapping')
                self.assertNotContains(self.get_as(user, '/search/suggest/?q=zanzibar'), 'zanzibar mapping')
        self.approve(self.held_question)
        self.approve(self.held_answer)
        self.assertTrue(SearchDocument.objects.filter(question_id=self.held_question.id).exists())
        self.assertIn('quokka', SearchDocument.objects.get(question_id=self.question.id).answers)
        self.assertContains(self.get_as(None, '/search/?q=zanzibar'), '<mark>zanzibar</mark> mapping')

    def test_feeds_and_sitemap(self):
        for url in ('/feeds/rss/', '/questions/?type=rss', '%s?type=rss&comments=yes' % self.question.get_absolute_url(),
                    '/sitemap-questions-1.xml'):
            with self.subTest(url=url):
                page = self.get_as(None, url).content.decode()
                self.assertNotIn('zanzibar', page)
                self.assertNotIn('quokka', page)
                self.assertNotIn('wombat', page)
        self.approve(self.held_question)
        self.assertIn('zanzibar', self.get_as(None, '/feeds/rss/').content.decode())

    def test_agent_exports(self):
        self.approve(self.held_answer)
        # The question with the approved answer is in the exports; the held question isn't.
        llms = self.get_as(None, '/llms.txt').content.decode()
        self.assertNotIn('zanzibar', llms)
        self.assertEqual(self.get_as(None, '/questions/%d.md' % self.held_question.id).status_code, 404)
        markdown = self.get_as(None, '/questions/%d.md' % self.question.id).content.decode()
        self.assertNotIn('wombat', markdown)
        export = b''.join(self.get_as(None, '/export/qa.jsonl').streaming_content).decode()
        self.assertNotIn('zanzibar', export)
        self.assertNotIn('wombat', export)

        def mcp(method, **params):
            body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            return self.client.post('/mcp', body, content_type='application/json').json()['result']
        self.client.logout()
        found = mcp('tools/call', name='search_forum', arguments={'query': 'zanzibar toolkit'})
        self.assertNotIn('zanzibar', found['content'][0]['text'])
        held = mcp('tools/call', name='get_question', arguments={'id': self.held_question.id})
        self.assertTrue(held['isError'])

    def test_related_and_similar_questions(self):
        other = Question(author=self.alice, title='Zanzibar toolkit download mirror', tagnames='zanzibar',
                         body='Another question body long enough.')
        other.save()
        self.assertNotIn(self.held_question, other.get_related_questions())
        self.assertNotIn(self.held_question, candidate_questions('zanzibar mapping toolkit'))
        self.login(self.bob)
        response = self.client.post(reverse('related_questions'), {'title': 'zanzibar mapping toolkit'})
        self.assertNotIn(TITLE, response.content.decode())

    def test_profile_lists_and_activity(self):
        profile = self.newbie.get_profile_url()
        answered = reverse('user_questions', kwargs={'mode': 'answered-by', 'user': self.newbie.id,
                                                     'slug': self.newbie.username})
        for user, sees in self.as_viewers():
            with self.subTest(user=user):
                page = self.get_as(user, profile).content.decode()
                self.assertEqual(TITLE in page, sees)
                recent = self.get_as(user, profile + '/recent/').content.decode()
                self.assertEqual(TITLE in recent, sees)
                answered_page = self.get_as(user, answered).content.decode()
                self.assertEqual(self.question.title in answered_page, sees)

    def test_ai_knowledge_and_suggestions(self):
        from forum_modules.ai import knowledge
        self.approve(self.held_answer)
        held_answer = self.answer(self.newbie, self.held_question, text='Held answer text with platypus inside.')
        self.assertTrue(held_answer.nis.pending_review)
        urls = [d['url'] for d in knowledge.forum_documents()]
        self.assertNotIn(self.held_question.get_absolute_url(), urls)
        self.assertIsNone(knowledge.thread_document(self.held_question))

    def test_moderator_sees_the_notice_and_inbox_link(self):
        page = self.get_as(self.mod, self.held_question.get_absolute_url()).content.decode()
        self.assertIn('Only its author and moderators can see this post', page)
        self.assertIn(reverse('admin_moderation'), page)


class NotificationTest(ReviewTestCase):
    def test_followers_hear_only_after_approval(self):
        collect = Tag.objects.create(name='zanzibar', created_by=self.alice)
        MarkedTag(user=self.bob, tag=collect, reason='good').save()

        question = self.ask()
        answer = self.answer()
        self.assertEqual(self.sent_mail, [])
        self.assertFalse(Notification.objects.filter(user=self.alice).exists())

        self.approve(question)
        self.assertTrue(any('zanzibar' in m[1] for m in self.sent_mail if m[0] == self.bob))
        self.approve(answer)
        self.assertTrue(any(m[0] == self.alice and 'New answer' in m[1] for m in self.sent_mail))
        self.assertTrue(Notification.objects.filter(user=self.alice, kind=Notification.ANSWER).exists())

    def test_author_hears_about_approval_and_removal(self):
        question = self.ask()
        self.approve(question)
        self.assertTrue(any(m[0] == self.newbie and 'published' in m[1] for m in self.sent_mail))
        self.assertTrue(Notification.objects.filter(user=self.newbie, kind=Notification.APPROVED).exists())

        other = self.create_user('other', email_isvalid=True)
        held = self.answer(other)
        self.login(self.mod)
        self.client.post(reverse('admin_moderation_post', args=[held.id]), {'action': 'delete'})
        self.assertTrue(fresh(held).nis.deleted)
        self.assertTrue(any(m[0] == other and 'not published' in m[1] for m in self.sent_mail))
        self.assertTrue(Notification.objects.filter(user=other, kind=Notification.REMOVED).exists())
        self.assertContains(self.get_as(other, reverse('notifications')), 'A moderator removed your post')


class InboxTest(ReviewTestCase):
    def test_staff_only_and_header_count(self):
        self.ask()
        for user in (None, self.bob):
            self.assertIn(self.get_as(user, reverse('admin_moderation')).status_code, (401, 403))
        self.assertNotContains(self.get_as(self.bob, '/'), 'moderation-link')
        home = self.get_as(self.mod, '/')
        self.assertContains(home, 'Moderation inbox: 1 waiting')
        page = self.get_as(self.mod, reverse('admin_moderation'))
        self.assertContains(page, TITLE)
        self.assertContains(page, 'One of the first posts of a new member')
        self.assertContains(page, 'Hidden until approved')

    def test_approve_from_the_inbox_and_undo(self):
        question = self.ask()
        self.login(self.mod)
        response = self.client.post(reverse('admin_moderation_post', args=[question.id]),
                                    {'action': 'approve', 'next': reverse('admin_moderation') + '?show=review'})
        self.assertRedirects(response, reverse('admin_moderation') + '?show=review', fetch_redirect_response=False)
        self.assertFalse(fresh(question).nis.pending_review)
        self.assertContains(self.client.get(reverse('admin_moderation')), 'Undo the last action')
        self.client.post(reverse('admin_moderation_undo'))
        self.assertTrue(fresh(question).nis.pending_review)
        self.assertEqual(self.get_as(self.bob, question.get_absolute_url()).status_code, 404)

    def test_open_redirects_are_refused(self):
        question = self.ask()
        self.login(self.mod)
        response = self.client.post(reverse('admin_moderation_post', args=[question.id]),
                                    {'action': 'approve', 'next': 'https://evil.example/'})
        self.assertRedirects(response, reverse('admin_moderation'), fetch_redirect_response=False)
        self.login(self.bob)
        self.assertIn(self.client.post(reverse('admin_moderation_post', args=[question.id]),
                                       {'action': 'delete'}).status_code, (401, 403))

    def test_delete_as_spam_suspends_and_removes_other_posts(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'after')
        question = self.ask()
        self.approve(question)
        answer = self.answer()
        comment, _ = self.comment(self.question)
        self.login(self.mod)
        self.client.post(reverse('admin_moderation_post', args=[answer.id]),
                         {'action': 'spam', 'suspend_author': '1', 'delete_other_posts': '1'})
        self.assertTrue(User.objects.get(id=self.newbie.id).is_suspended())
        for node in (question, answer, comment):
            self.assertTrue(fresh(node).nis.deleted, node)
        self.assertEqual(review.inbox_counts()['review'], 0)
        # Undo gives everything back.
        self.client.post(reverse('admin_moderation_undo'))
        self.assertFalse(User.objects.get(id=self.newbie.id).is_suspended())
        self.assertFalse(fresh(question).nis.deleted)

    def test_delete_as_spam_without_options_keeps_the_rest(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'after')
        question = self.ask()
        answer = self.answer()
        self.login(self.mod)
        self.client.post(reverse('admin_moderation_post', args=[answer.id]), {'action': 'spam'})
        self.assertTrue(fresh(answer).nis.deleted)
        self.assertFalse(fresh(question).nis.deleted)
        self.assertTrue(User.objects.get(id=self.newbie.id).is_active)

    def test_flags_dismiss_and_suspend(self):
        FlagAction(user=self.bob, node=self.question, extra='<b>spam</b>', ip='127.0.0.1').save()
        page = self.get_as(self.mod, reverse('admin_moderation') + '?show=flags')
        self.assertContains(page, '&lt;b&gt;spam&lt;/b&gt;')
        self.assertContains(page, self.question.title)
        self.client.post(reverse('admin_moderation_post', args=[self.question.id]), {'action': 'dismiss'})
        self.assertFalse(Action.objects.filter(action_type='flag', canceled=False).exists())
        self.assertEqual(review.inbox_counts()['flags'], 0)
        self.client.post(reverse('admin_moderation_post', args=[self.question.id]), {'action': 'suspend'})
        self.assertTrue(User.objects.get(id=self.alice.id).is_suspended())
        # Moderators are never suspended.
        moderated = Question(author=self.mod, title='A moderator question title', tagnames='x', body='Long enough body.')
        moderated.save()
        self.client.post(reverse('admin_moderation_post', args=[moderated.id]), {'action': 'suspend'})
        self.assertTrue(User.objects.get(id=self.mod.id).is_active)

    def test_reported_users(self):
        from forum import REQUEST_HOLDER  # noqa: F401  (ReportAction emails superusers)
        ReportAction(user=self.bob, ip='127.0.0.1').save(data={'reported': self.newbie, 'publicmsg': 'Spam links'})
        self.assertEqual(review.inbox_counts()['users'], 1)
        page = self.get_as(self.mod, reverse('admin_moderation') + '?show=users')
        self.assertContains(page, 'Spam links')
        self.client.post(reverse('admin_moderation_user', args=[self.newbie.id]), {'action': 'dismiss'})
        self.assertEqual(review.inbox_counts()['users'], 0)
        self.assertTrue(User.objects.get(id=self.newbie.id).is_active)

        ReportAction(user=self.bob, ip='127.0.0.1').save(data={'reported': self.newbie, 'publicmsg': 'Spam again'})
        self.set(settings.FIRST_POSTS_MODERATION, 'off')
        answer = self.answer()
        self.login(self.mod)
        self.client.post(reverse('admin_moderation_user', args=[self.newbie.id]), {'action': 'spam'})
        self.assertFalse(User.objects.get(id=self.newbie.id).is_active)
        self.assertTrue(fresh(answer).nis.deleted)

    def test_ai_reports_delete_as_spam(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'off')
        answer = self.answer()
        report = ModerationReport.objects.create(node=answer, source='ai', verdict='spam', confidence=0.9)
        page = self.get_as(self.mod, reverse('admin_moderation') + '?show=ai')
        self.assertContains(page, 'Reported by automated checks')
        self.client.post(reverse('admin_resolve_moderation_report', args=[report.id]),
                         {'resolution': 'spam', 'suspend_author': '1', 'next': reverse('admin_moderation') + '?show=ai'})
        report.refresh_from_db()
        self.assertEqual(report.resolution, 'deleted')
        self.assertTrue(fresh(answer).nis.deleted)
        self.assertFalse(User.objects.get(id=self.newbie.id).is_active)

    def test_old_flagged_posts_url_shows_the_inbox(self):
        self.assertContains(self.get_as(self.mod, reverse('admin_flagged_posts')), 'Moderation inbox')

    def test_settings_page(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.get_as(admin, reverse('admin_set', args=['moderation']))
        self.assertContains(page, "Review new members&#x27; first posts")
        self.assertContains(page, 'Review before publishing')
