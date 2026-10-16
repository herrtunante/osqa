"""First posts review ("before" mode) and the moderation inbox: a newcomer's
answer is held (with the notice and the "Waiting for review" badge), other
members don't see it, a moderator approves it from the inbox on a phone and
the asker gets the email; deleting a spammer's post as spam (with the
confirmation dialog) suspends the account and removes its other posts; the
inbox count in the moderator's header."""
from django.urls import reverse

from forum import settings
from forum.models import Answer, Question, User

from .base import E2ETestCase


class ReviewQueueTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.moderator = self.create_user('moderator', reputation=10, is_staff=True)
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bob', reputation=500)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')
        self.create_question(self.bob, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')
        self.newcomer = self.create_user('newcomer', reputation=1)
        # Turned on after the members above have their (approved) posts.
        self.set_setting(settings.FIRST_POSTS_MODERATION, 'before')

    def answer(self, page, question, text):
        self.visit(page, question.get_absolute_url())
        page.fill('#fmanswer #editor', text)
        with page.expect_navigation():
            page.click('#fmanswer input[type=button]')
        self.check_page(page)
        return Answer.objects.filter(parent=question).latest('id')

    def test_newcomer_answer_is_held_until_approved_on_a_phone(self):
        text = 'Open the survey in the designer and use Export, then CSV.'
        newcomer = self.open(self.newcomer)
        answer = self.answer(newcomer, self.question, text)
        self.assertTrue(answer.nis.pending_review)
        self.assertIn('Your post will appear once a moderator has reviewed it', newcomer.content())
        notice = newcomer.locator('#answer-container-%d .review-notice' % answer.id)
        self.assertIn('Waiting for review', notice.inner_text())
        self.assertEqual(self.mail_to(self.alice), [])

        # Another member and an anonymous visitor don't see it.
        for viewer in (self.open(self.bob), self.open()):
            self.visit(viewer, self.question.get_absolute_url())
            self.assertNotIn(text, viewer.content())
            self.assertEqual(viewer.locator('.answer').count(), 0)

        # The moderator sees the count in the header and approves it from the inbox, on a phone.
        moderator = self.open(self.moderator, device='mobile', dark=True)
        self.visit(moderator, '/')
        link = moderator.locator('#moderation-link')
        self.assertEqual(link.locator('.moderation-count').inner_text(), '1')
        self.assertIn('1 waiting', link.get_attribute('aria-label'))
        with moderator.expect_navigation():
            link.click()
        self.check_page(moderator)
        card = moderator.locator('#review-%d' % answer.id)
        self.assertIn(text, card.inner_text())
        self.assertIn('newcomer', card.locator('.mod-author').inner_text())
        self.assertIn('One of the first posts of a new member', card.inner_text())
        with moderator.expect_navigation():
            card.get_by_role('button', name='Approve').click()
        self.check_page(moderator)
        self.assertIn('The post is approved.', moderator.content())
        self.assertFalse(Answer.objects.get(id=answer.id).nis.pending_review)
        self.assertEqual(moderator.locator('#review-%d' % answer.id).count(), 0)
        self.assertEqual(moderator.locator('#moderation-link .moderation-count').count(), 0)

        # The asker gets the "new answer" email now, and the newcomer hears it was approved.
        self.assertTrue(any('New answer' in m[1] for m in self.mail_to(self.alice)), self.mail_to(self.alice))
        self.assertTrue(any('published' in m[1] for m in self.mail_to(self.newcomer)))
        bob = self.open(self.bob)
        self.visit(bob, self.question.get_absolute_url())
        self.assertIn(text, bob.content())
        self.assertEqual(bob.locator('.review-notice').count(), 0)

    def test_held_question_and_its_notice(self):
        newcomer = self.open(self.newcomer)
        self.visit(newcomer, '/questions/ask/')
        newcomer.fill('#id_title', 'Where do I download the Collect Earth plugin?')
        newcomer.fill('#editor', 'I looked on the website but could not find the download link.')
        newcomer.fill('#id_tags', 'collect-earth')
        newcomer.keyboard.press('Escape')
        with newcomer.expect_navigation():
            newcomer.click('#fmask input[name=ask]')
        self.check_page(newcomer)
        question = Question.objects.get(title='Where do I download the Collect Earth plugin?')
        self.assertIn('Your post will appear once a moderator has reviewed it', newcomer.content())
        self.assertIn('Waiting for review', newcomer.locator('#question-table .review-notice').inner_text())
        self.visit(newcomer, '/questions/')
        self.assertIn('Waiting for review', newcomer.locator('.question-row', has_text='Collect Earth plugin').inner_text())

        anonymous = self.open()
        self.visit(anonymous, '/questions/')
        self.assertNotIn('Collect Earth plugin', anonymous.content())
        response = anonymous.goto(self.url(question.get_absolute_url()))
        self.assertEqual(response.status, 404)
        anonymous.problems.clear()

    def test_delete_as_spam(self):
        self.set_setting(settings.FIRST_POSTS_MODERATION, 'after')
        spammer = self.create_user('spammer', reputation=1)
        spam_question = self.create_question(spammer, 'Cheap watches at a great discount, buy now', tags='watches')
        spam_answer = self.create_answer(spammer, self.question, 'Buy cheap watches at example dot com today.')

        page = self.open(self.moderator)
        self.visit(page, reverse('admin_moderation') + '?show=review')
        self.assertIn('2', page.locator('.mod-filters a.on .mod-count').inner_text())
        card = page.locator('#review-%d' % spam_answer.id)
        card.get_by_role('button', name='Delete as spam').click()
        dialog = page.locator('#spam-review-%d' % spam_answer.id)
        dialog.wait_for(state='visible')
        self.assertTrue(dialog.locator('input[name=suspend_author]').is_checked())
        self.assertIn('Delete their other post', dialog.inner_text())
        # Cancel closes it without doing anything; Escape too.
        dialog.get_by_role('button', name='Cancel').click()
        dialog.wait_for(state='hidden')
        self.assertFalse(Answer.objects.get(id=spam_answer.id).nis.deleted)

        card.get_by_role('button', name='Delete as spam').click()
        dialog.wait_for(state='visible')
        with page.expect_navigation():
            dialog.get_by_role('button', name='Delete as spam').click()
        self.check_page(page)
        self.assertIn('2 posts deleted as spam.', page.content())
        self.assertIn('The account spammer has been suspended.', page.content())
        self.assertTrue(Answer.objects.get(id=spam_answer.id).nis.deleted)
        self.assertTrue(Question.objects.get(id=spam_question.id).nis.deleted)
        self.assertTrue(User.objects.get(id=spammer.id).is_suspended())
        self.assertEqual(page.locator('.mod-card').count(), 0)
        self.assertIn('Nothing is waiting in this list.', page.content())

        # Undo puts everything back.
        with page.expect_navigation():
            page.get_by_role('button', name='Undo the last action').click()
        self.check_page(page)
        self.assertFalse(Answer.objects.get(id=spam_answer.id).nis.deleted)
        self.assertFalse(User.objects.get(id=spammer.id).is_suspended())

    def test_inbox_from_the_keyboard_and_counts(self):
        self.create_answer(self.newcomer, self.question, 'A held answer that is long enough to post.')
        flagger = self.create_user('flagger', reputation=500)
        page = self.open(flagger)
        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % self.question.id)
        page.click('a[href^="/flag/%d/"]' % self.question.id)
        self.confirm_dialog(page, {'textarea[name=prompt]': 'spam'})

        page = self.open(self.moderator)
        self.visit(page, '/')
        self.assertEqual(page.locator('#moderation-link .moderation-count').inner_text(), '2')
        self.visit(page, reverse('admin_moderation'))
        labels = page.locator('.mod-filters a').all_inner_texts()
        self.assertEqual([' '.join(label.split()) for label in labels],
                         ['Everything 2', 'First posts 1', 'Flagged posts 1', 'Reported users 0',
                          'AI moderation reports 0', 'Suggested edits 0'])
        page.locator('body').press('j')
        self.assertEqual(page.evaluate('document.activeElement.className'), 'mod-card mod-review')
        page.keyboard.press('j')
        self.assertEqual(page.evaluate('document.activeElement.className'), 'mod-card mod-flagged')
        page.keyboard.press('k')
        self.assertEqual(page.evaluate('document.activeElement.className'), 'mod-card mod-review')
        with page.expect_navigation():
            page.locator('#flagged-%d' % self.question.id).get_by_role('button', name='Dismiss').click()
        self.check_page(page)
        self.assertIn('1 flag dismissed', page.content())
        self.visit(page, reverse('admin_moderation') + '?show=flags')
        self.assertIn('Nothing is waiting in this list.', page.content())
        # The old Flagged posts page shows the inbox.
        self.visit(page, reverse('admin_flagged_posts'))
        self.assertIn('Moderation inbox', page.title())

    def test_reported_user_suspended_from_the_inbox(self):
        # bob reports the newcomer from the profile; a moderator suspends the
        # account from the inbox's Reported users list on a phone.
        page = self.open(self.bob)
        self.visit(page, self.newcomer.get_profile_url())
        page.click('a[href="%s"]' % reverse('user_report', kwargs={'id': self.newcomer.id}))
        self.confirm_dialog(page, {'textarea[name=publicmsg]': 'Signs up and posts shop links'})

        page = self.open(self.moderator, device='mobile', dark=True)
        self.visit(page, reverse('admin_moderation') + '?show=users')
        card = page.locator('#user-%d' % self.newcomer.id)
        self.assertIn('Signs up and posts shop links', card.inner_text())
        with page.expect_navigation():
            card.get_by_role('button', name='Suspend').click()
        self.check_page(page)
        self.assertIn('has been suspended', page.content())
        self.assertTrue(User.objects.get(id=self.newcomer.id).is_suspended())
        self.assertIn('Nothing is waiting in this list.', page.content())
