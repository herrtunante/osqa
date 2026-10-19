"""Suggested edits (forum/utils/suggested_edits.py, forum/views/suggested_edits.py):
who sees "Suggest an edit", one pending suggestion per post, the daily limit,
approving (a revision credited to the suggester, reputation with its cap,
undo), improving, rejecting with a reason, superseded suggestions, the
notifications and emails, and who sees which suggestions in the review queue
(held and deleted posts only for moderators)."""
from django.urls import reverse

from forum import settings
from forum.actions import DeleteAction, ReviseAction
from forum.models import (Action, Answer, Node, Notification, PendingEmail, Question, SuggestedEdit, Tag,
                          User)
from forum.utils import suggested_edits as sugg

from .base import OSQATestCase

BODY = 'How do I export the survey data from Collect to a CSV file for R?'
NEW_BODY = 'How do I export the survey data from Collect to a CSV file, to analyse it in R?'


def fresh(obj):
    return type(obj).objects.get(id=obj.id)


class SuggestedEditTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100, email_isvalid=True)
        self.sam = self.create_user('sam', reputation=50, email_isvalid=True)
        self.tom = self.create_user('tom', reputation=50, email_isvalid=True)
        self.rita = self.create_user('rita', reputation=2500, email_isvalid=True)
        self.mod = self.create_user('mod', reputation=10, is_staff=True, email_isvalid=True)
        Tag.objects.get_or_create(name='collect', defaults={'created_by': self.alice})
        Tag.objects.get_or_create(name='csv', defaults={'created_by': self.alice})
        self.question = Question(author=self.alice, title='Exporting Collect survey data to CSV',
                                 tagnames='collect', body=BODY)
        self.question.save()
        self.answer = Answer(author=self.alice, parent=self.question,
                             body='Use the Export button of the survey and pick CSV.')
        self.answer.save()

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def suggest(self, user=None, post=None, follow=False, **data):
        post = post or self.question
        self.login(user or self.sam)
        fields = {'text': NEW_BODY, 'summary': 'Said what the data is for'}
        if post.node_type == 'question':
            fields.update(title=post.title, tags=post.tagnames)
        fields.update(data)
        return self.client.post(reverse('suggest_edit', args=[post.id]), fields, follow=follow)

    def pending(self, post=None):
        return SuggestedEdit.objects.get(node_id=(post or self.question).id, status=SuggestedEdit.PENDING)

    def review(self, suggestion, user=None, **data):
        self.login(user or self.rita)
        return self.client.post(reverse('suggested_edit_review', args=[suggestion.id]), data)

    def controls(self, user, post=None):
        self.client.logout()
        if user is not None:
            self.login(user)
        page = self.client.get(self.question.get_absolute_url()).content.decode()
        return page


class SuggestingTest(SuggestedEditTestCase):
    def test_who_sees_suggest_an_edit(self):
        suggest_url = reverse('suggest_edit', args=[self.question.id])
        page = self.controls(self.sam)
        self.assertIn(suggest_url, page)
        self.assertIn(reverse('suggest_edit', args=[self.answer.id]), page)
        self.assertNotIn(reverse('edit_question', args=[self.question.id]), page)
        # The author and members who may edit others' posts edit directly; visitors see neither.
        for user in (self.alice, self.rita, self.mod, None):
            with self.subTest(user=user):
                self.assertNotIn(suggest_url, self.controls(user))
        # Suspended members can't suggest.
        self.tom.is_active = False
        self.tom.save()
        self.assertFalse(sugg.can_suggest(self.tom, self.question))
        # Nor anyone when the feature is off.
        self.set(settings.SUGGESTED_EDITS_ENABLED, False)
        self.assertNotIn(suggest_url, self.controls(self.sam))
        self.login(self.sam)
        self.assertEqual(self.client.get(suggest_url).status_code, 403)

    def test_edit_page_explains_and_requires_a_summary(self):
        self.login(self.sam)
        page = self.client.get(reverse('suggest_edit', args=[self.question.id])).content.decode()
        self.assertIn('Your edit will be reviewed', page)
        self.assertIn('reviewers read it first', page)
        self.assertIn('Suggest edit', page)
        self.assertNotIn('id_revision', page)
        response = self.suggest(summary='')
        self.assertEqual(response.status_code, 200)
        self.assertIn('This field is required', response.content.decode())
        response = self.suggest(text=BODY)
        self.assertIn("You haven&#x27;t changed anything yet.", response.content.decode())
        self.assertFalse(SuggestedEdit.objects.exists())
        # Members who can edit are sent to the edit page.
        self.login(self.rita)
        response = self.client.get(reverse('suggest_edit', args=[self.question.id]))
        self.assertRedirects(response, reverse('edit_question', args=[self.question.id]), fetch_redirect_response=False)

    def test_suggestion_is_saved_for_review(self):
        response = self.suggest(follow=True)
        self.assertIn('Your edit will appear on the post once a reviewer has approved it', response.content.decode())
        suggestion = self.pending()
        self.assertEqual(suggestion.author, self.sam)
        self.assertEqual(suggestion.body, NEW_BODY)
        self.assertEqual(suggestion.base_revision, fresh(self.question).active_revision)
        self.assertEqual(fresh(self.question).body.count('analyse'), 0)  # not applied yet

    def test_answer_suggestion(self):
        self.suggest(post=self.answer, text='Use the Export button of the survey, then pick CSV and save.')
        suggestion = self.pending(self.answer)
        self.assertEqual(suggestion.title, '')
        self.review(suggestion, action='approve')
        self.assertIn('then pick CSV', fresh(self.answer).active_revision.body)
        self.assertEqual(fresh(self.answer).active_revision.author, self.sam)

    def test_one_pending_suggestion_per_post(self):
        self.suggest()
        response = self.suggest(user=self.tom, follow=True)
        self.assertIn('An edit to this post is already waiting for review', response.content.decode())
        self.assertEqual(SuggestedEdit.objects.count(), 1)
        page = self.controls(self.tom)
        self.assertIn('edit pending', page)
        self.assertIn('An edit is waiting for review', page)
        self.assertNotIn(reverse('suggest_edit', args=[self.question.id]), page)
        # Another post is fine.
        self.suggest(user=self.tom, post=self.answer, text='Use the Export button and pick CSV, then save it.')
        self.assertEqual(SuggestedEdit.objects.count(), 2)

    def test_daily_limit(self):
        self.set(settings.SUGGESTED_EDITS_PER_DAY, 1)
        self.suggest()
        response = self.suggest(post=self.answer, text='Use Export and pick CSV in the dialog.', follow=True)
        self.assertIn('You have suggested as many edits as you can in a day', response.content.decode())
        self.assertEqual(SuggestedEdit.objects.count(), 1)
        self.set(settings.SUGGESTED_EDITS_PER_DAY, 0)
        self.suggest(post=self.answer, text='Use Export and pick CSV in the dialog.')
        self.assertEqual(SuggestedEdit.objects.count(), 2)


class ReviewingTest(SuggestedEditTestCase):
    def setUp(self):
        super().setUp()
        self.suggest(summary='Said what the data is for <b>R</b>')
        self.suggestion = self.pending()
        self.sent_mail.clear()

    def test_approving_credits_the_suggester(self):
        response = self.review(self.suggestion, action='approve', next=reverse('suggested_edits'))
        self.assertRedirects(response, reverse('suggested_edits'), fetch_redirect_response=False)
        question = fresh(self.question)
        revision = question.active_revision
        self.assertEqual(revision.author, self.sam)
        self.assertEqual(revision.body, NEW_BODY)
        self.assertIn('approved by rita', revision.summary)
        self.assertIn('analyse', question.body)
        suggestion = fresh(self.suggestion)
        self.assertEqual(suggestion.status, SuggestedEdit.APPROVED)
        self.assertEqual(suggestion.reviewer, self.rita)
        action = suggestion.action.leaf
        self.assertEqual((action.user, action.real_user), (self.sam, self.rita))
        self.assertEqual(question.last_edited.by, self.sam)
        # +2 reputation for the suggester, through the action (undoable).
        self.assertEqual(fresh(self.sam).reputation, 52)
        description = action.describe(self.alice)
        self.assertIn('suggested edit approved by', description)
        self.assertIn('>rita</a>', description)
        # In-site notification and email.
        notice = Notification.objects.get(user=self.sam)
        self.assertEqual((notice.kind, notice.actor), (Notification.EDIT_APPROVED, self.rita))
        mail = [m for m in self.sent_mail if m[0] == self.sam]
        self.assertEqual(len(mail), 1)
        self.assertIn('Your edit was approved: Exporting Collect survey data to CSV', mail[0][1])
        self.assertIn('Said what the data is for &lt;b&gt;R&lt;/b&gt;', mail[0][2])
        self.assertIn('Said what the data is for <b>R</b>', mail[0][3])
        self.login(self.sam)
        page = self.client.get(reverse('notifications')).content.decode()
        self.assertIn('approved the edit you suggested', page)

    def test_reputation_cap(self):
        self.set(settings.MAX_REP_BY_SUGGESTED_EDITS, 3)
        self.review(self.suggestion, action='approve')
        self.suggest(post=self.answer, text='Use the Export button and pick CSV, then save it.')
        self.review(self.pending(self.answer), action='approve')
        self.assertEqual(fresh(self.sam).reputation, 50 + 2 + 1)
        self.assertEqual(sugg.rep_gain_for(fresh(self.sam)), 0)

    def test_undo_approval(self):
        self.review(self.suggestion, action='approve')
        self.assertEqual(fresh(self.question).active_revision.revision, 2)
        response = self.client.post(reverse('suggested_edit_undo'), {'next': reverse('suggested_edits')})
        self.assertEqual(response.status_code, 302)
        question = fresh(self.question)
        self.assertEqual(question.active_revision.revision, 1)
        self.assertNotIn('analyse', question.body)
        self.assertEqual(fresh(self.sam).reputation, 50)
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        # It can be reviewed again.
        self.review(self.suggestion, action='approve')
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.APPROVED)

    def test_rejecting_needs_a_reason(self):
        self.review(self.suggestion, action='reject')
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        self.review(self.suggestion, action='reject', reason='other')
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        self.review(self.suggestion, action='reject', reason='other', comment='Keep the <i>original</i> words')
        suggestion = fresh(self.suggestion)
        self.assertEqual((suggestion.status, suggestion.reason), (SuggestedEdit.REJECTED, 'other'))
        self.assertEqual(fresh(self.question).active_revision.revision, 1)
        self.assertEqual(fresh(self.sam).reputation, 50)
        description = suggestion.action.leaf.describe(None)
        self.assertIn('rejected an edit suggested by', description)
        # Undo puts it back in the queue.
        self.client.post(reverse('suggested_edit_undo'))
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        notice = Notification.objects.get(user=self.sam)
        self.assertEqual(notice.kind, Notification.EDIT_REJECTED)
        self.assertIn('Keep the <i>original</i> words', notice.text)
        self.login(self.sam)
        page = self.client.get(reverse('notifications')).content.decode()
        self.assertIn('Keep the &lt;i&gt;original&lt;/i&gt; words', page)
        mail = [m for m in self.sent_mail if m[0] == self.sam]
        self.assertIn('Your edit was not approved', mail[0][1])
        self.assertIn('Another reason', mail[0][3])
        self.assertIn('Keep the &lt;i&gt;original&lt;/i&gt; words', mail[0][2])

    def test_improve(self):
        self.login(self.rita)
        page = self.client.get(reverse('suggested_edit_improve', args=[self.suggestion.id])).content.decode()
        self.assertIn('You are improving an edit suggested by sam', page)
        self.assertIn('analyse it in R', page)
        response = self.client.post(reverse('suggested_edit_improve', args=[self.suggestion.id]), {
            'title': self.question.title, 'tags': 'collect csv', 'summary': 'Added the csv tag',
            'text': NEW_BODY + ' (Collect 2.0)'})
        self.assertEqual(response.status_code, 302)
        revisions = list(fresh(self.question).revisions.order_by('revision'))
        self.assertEqual([r.author.username for r in revisions], ['alice', 'sam', 'rita'])
        self.assertEqual(revisions[1].body, NEW_BODY)
        self.assertEqual(revisions[2].tagnames, 'collect csv')
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.APPROVED)
        self.assertEqual(fresh(self.sam).reputation, 52)

    def test_conflicting_edit_supersedes(self):
        # The author rewrites the body: the suggestion can't be applied any more.
        self.login(self.alice)
        ReviseAction(user=self.alice, node=fresh(self.question), ip='127.0.0.1').save(data={
            'title': self.question.title, 'tags': 'collect', 'text': BODY + ' (Collect 1.4)', 'summary': 'version'})
        suggestion = fresh(self.suggestion)
        self.assertEqual((suggestion.status, suggestion.reason), (SuggestedEdit.SUPERSEDED, 'conflict'))
        response = self.review(self.suggestion, action='approve')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('analyse', fresh(self.question).body)
        # A new suggestion may be made now.
        self.assertTrue(sugg.can_suggest(self.tom, fresh(self.question)))
        self.assertIsNone(sugg.pending_for(self.question))

    def test_other_changes_are_kept(self):
        # The author changes the title only: approving keeps it and applies the body.
        ReviseAction(user=self.alice, node=fresh(self.question), ip='127.0.0.1').save(data={
            'title': 'Exporting Collect survey data to a CSV file', 'tags': 'collect', 'text': BODY,
            'summary': 'title'})
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        self.login(self.rita)
        page = self.client.get(reverse('suggested_edits')).content.decode()
        self.assertIn('approving it keeps those changes', page)
        self.review(self.suggestion, action='approve')
        question = fresh(self.question)
        self.assertEqual(question.title, 'Exporting Collect survey data to a CSV file')
        self.assertIn('analyse', question.body)

    def test_conflict_found_when_approving(self):
        # Changed without the hook (e.g. an import): checked again on approval.
        question = fresh(self.question)
        question.create_revision(self.alice, title=question.title, tagnames='collect', body=BODY + ' again',
                                 summary='x')
        self.review(self.suggestion, action='approve')
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.SUPERSEDED)

    def test_who_may_review(self):
        url = reverse('suggested_edits')
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.sam)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.review(self.suggestion, user=self.tom, action='approve').status_code, 403)
        self.assertEqual(self.client.get(reverse('suggested_edit_improve', args=[self.suggestion.id])).status_code,
                         403)
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        for reviewer in (self.rita, self.mod):
            self.login(reviewer)
            page = self.client.get(url).content.decode()
            self.assertIn('suggested-edit-%d' % self.suggestion.id, page)
            self.assertIn('<del>', page.replace('<del >', '<del>'))
        # Nobody reviews their own suggestion (a suggester who reached the reputation since).
        User.objects.filter(id=self.sam.id).update(reputation=3000)
        self.assertFalse(sugg.can_review(fresh(self.sam), self.suggestion))

    def test_header_link_for_reviewers(self):
        self.login(self.rita)
        page = self.client.get('/').content.decode()
        self.assertIn('id="suggested-edits-link"', page)
        self.assertIn('Review suggested edits: 1 waiting', page)
        self.login(self.sam)
        self.assertNotIn('id="suggested-edits-link"', self.client.get('/').content.decode())
        # Moderators see it in the moderation inbox count instead.
        self.login(self.mod)
        page = self.client.get('/').content.decode()
        self.assertNotIn('id="suggested-edits-link"', page)
        self.assertIn('Moderation inbox: 1 waiting', page)

    def test_moderation_inbox(self):
        self.login(self.mod)
        inbox = reverse('admin_moderation')
        page = self.client.get(inbox + '?show=edits').content.decode()
        self.assertIn('id="suggested-edit-%d"' % self.suggestion.id, page)
        self.assertIn('Said what the data is for &lt;b&gt;R&lt;/b&gt;', page)
        response = self.review(self.suggestion, user=self.mod, action='approve', next=inbox + '?show=edits')
        self.assertRedirects(response, inbox + '?show=edits', fetch_redirect_response=False)
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.APPROVED)
        # The inbox's undo cancels it.
        self.client.post(reverse('admin_moderation_undo'), {'next': inbox})
        self.assertEqual(fresh(self.suggestion).status, SuggestedEdit.PENDING)
        self.assertEqual(fresh(self.question).active_revision.revision, 1)

    def test_daily_summary(self):
        prefs = self.sam.subscription_settings
        prefs.email_frequency = 'd'
        prefs.save()
        self.review(self.suggestion, action='reject', reason='no_improvement')
        self.assertEqual([m for m in self.sent_mail if m[0] == self.sam], [])
        self.assertEqual(PendingEmail.objects.get(user=self.sam).kind, PendingEmail.EDIT_REJECTED)
        from forum.utils.email_frequency import send_summaries
        send_summaries()
        mail = [m for m in self.sent_mail if m[0].id == self.sam.id]
        self.assertEqual(len(mail), 1)
        self.assertIn('Your suggested edits', mail[0][3])
        self.assertIn('rita rejected the edit you suggested.', mail[0][3])

    def test_notifications_off(self):
        prefs = self.sam.subscription_settings
        prefs.enable_notifications = False
        prefs.save()
        self.review(self.suggestion, action='approve')
        self.assertEqual([m for m in self.sent_mail if m[0] == self.sam], [])
        # The in-site notification doesn't depend on the emails.
        self.assertTrue(Notification.objects.filter(user=self.sam, kind=Notification.EDIT_APPROVED).exists())


class HeldAndDeletedPostsTest(SuggestedEditTestCase):
    def test_held_posts_are_reviewed_by_moderators_only(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'before')
        newbie = self.create_user('newbie', reputation=60, email_isvalid=True)
        self.login(newbie)
        self.client.post(reverse('answer', args=[self.question.id]), {'text': 'A held answer long enough to post it.'})
        held = Answer.objects.filter(parent=self.question).latest('id')
        self.assertTrue(held.nis.pending_review)
        # Other members can't see it, so they can't suggest an edit to it.
        self.assertFalse(sugg.can_suggest(self.sam, held))
        self.login(self.sam)
        self.assertEqual(self.client.get(reverse('suggest_edit', args=[held.id])).status_code, 404)
        # A suggestion made before it was held: only moderators see it in the queue.
        suggestion = SuggestedEdit.objects.create(node=held, author=self.sam, body='A held answer, long enough.',
                                                  summary='typo', base_revision=held.active_revision)
        self.assertNotIn(suggestion, list(sugg.queue_for(self.rita)))
        self.assertFalse(sugg.can_review(self.rita, suggestion))
        self.assertIn(suggestion, list(sugg.queue_for(self.mod)))
        self.assertTrue(sugg.can_review(self.mod, suggestion))
        self.assertEqual(sugg.pending_count(), 0)
        self.login(self.rita)
        self.assertNotIn('A held answer', self.client.get(reverse('suggested_edits')).content.decode())
        self.assertEqual(self.review(suggestion, action='approve').status_code, 403)
        self.login(self.mod)
        self.assertIn('suggested-edit-%d' % suggestion.id,
                      self.client.get(reverse('admin_moderation') + '?show=edits').content.decode())

    def test_deleted_posts(self):
        self.suggest()
        suggestion = self.pending()
        DeleteAction(user=self.mod, node=fresh(self.question), ip='127.0.0.1').save()
        self.assertNotIn(suggestion, list(sugg.queue_for(self.rita)))
        self.assertIn(suggestion, list(sugg.queue_for(self.mod)))
        self.assertFalse(sugg.can_suggest(self.tom, Node.objects.get(id=self.question.id).leaf))

    def test_actions_are_proxies(self):
        self.suggest()
        self.review(self.pending(), action='approve')
        self.assertTrue(Action.objects.filter(action_type='suggestededit').exists())
