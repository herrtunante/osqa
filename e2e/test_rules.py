"""The rules behind the buttons, clicked through the real interface: who may
vote, accept, report, comment and award points, the messages people see when
they may not, and what each action does to scores and reputation."""
import datetime
import re

from django.urls import reverse

from forum import settings
from forum.actions.meta import AcceptAnswerAction, DeleteAction, FlagAction, VoteDownAction, VoteUpAction
from forum.actions.node import RetagAction
from forum.models import Answer, Comment, Question, User
from forum.models.action import Action

from .base import E2ETestCase


class RulesTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)   # asks
        self.bob = self.create_user('bob', reputation=100)       # answers
        self.carol = self.create_user('carol', reputation=500)   # votes, reports, retags
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')
        self.answer = self.create_answer(self.bob, self.question, 'Open the survey and use Export > CSV.')

    # -- helpers ----------------------------------------------------------

    def rep(self, user):
        return User.objects.filter(id=user.id).values_list('reputation', flat=True)[0]

    def score(self, node):
        return type(node).objects.filter(id=node.id).values_list('score', flat=True)[0]

    def shown_score(self, page, node):
        return page.locator('#post-%d-score' % node.id).inner_text().strip()

    def expect_message(self, page, text):
        """The command's message dialog says `text`; it is then closed."""
        dialog = page.locator('.dialog.warning').filter(has_text=text)
        dialog.wait_for()
        self.wait_for_ajax(page)
        self.check_page(page)
        dialog.locator('.dialog-yes').click()
        dialog.wait_for(state='detached')

    def click_and_expect(self, page, selector, text):
        page.click(selector)
        self.expect_message(page, text)

    def report(self, page, post, reason):
        page.click('#node-%d-menu-trigger' % post.id)
        page.click('#node-%d-menu-dropdown a[href^="/flag/%d/"]' % (post.id, post.id))
        page.wait_for_selector('.dialog.prompt textarea[name=prompt]')
        page.fill('.dialog.prompt textarea[name=prompt]', reason)
        page.click('.dialog.prompt .dialog-yes')

    # -- voting -----------------------------------------------------------

    def test_who_may_vote(self):
        upvote = '#post-%d-upvote' % self.answer.id

        page = self.open()
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, upvote, 'anonymous users cannot vote')

        newbie = self.create_user('newbie', reputation=1)
        page = self.open(newbie)
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, upvote, "you don't have enough reputation points to upvote")
        self.assertEqual(self.shown_score(page, self.answer), '0')

        # Only an up vote's own minimum counts, even when down votes need less.
        self.set_setting(settings.REP_TO_VOTE_DOWN, 1)
        self.set_setting(settings.REP_TO_VOTE_UP, 50)
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, upvote, 'The minimum reputation required is 50 (yours is 1)')
        self.assertEqual(self.score(self.answer), 0)
        self.assertEqual(self.rep(self.bob), 100)

    def test_down_vote_and_taking_votes_back(self):
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())

        # A down vote costs the author 2 points and the voter 1.
        self.click_command(page, '#post-%d-downvote' % self.answer.id)
        self.assertEqual(self.shown_score(page, self.answer), '-1')
        self.assertEqual(self.score(self.answer), -1)
        self.assertEqual(self.rep(self.bob), 100 - int(settings.REP_LOST_BY_DOWNVOTED))
        self.assertEqual(self.rep(self.carol), 500 - int(settings.REP_LOST_BY_DOWNVOTING))
        self.assertEqual(page.locator('#post-%d-downvote.on' % self.answer.id).count(), 1)

        # Clicking it again takes the vote back.
        self.click_command(page, '#post-%d-downvote' % self.answer.id)
        self.assertEqual(self.shown_score(page, self.answer), '0')
        self.assertEqual((self.score(self.answer), self.rep(self.bob), self.rep(self.carol)), (0, 100, 500))

        # Up, then up again: cancelled, score and reputation as before.
        self.click_command(page, '#post-%d-upvote' % self.answer.id)
        self.assertEqual(self.shown_score(page, self.answer), '1')
        self.assertEqual(self.rep(self.bob), 100 + int(settings.REP_GAIN_BY_UPVOTED))
        self.click_command(page, '#post-%d-upvote' % self.answer.id)
        self.assertEqual(self.shown_score(page, self.answer), '0')
        self.assertEqual((self.score(self.answer), self.rep(self.bob)), (0, 100))
        self.assertEqual(page.locator('#post-%d-upvote.on' % self.answer.id).count(), 0)

        # Up, then down: the down click takes the up vote back, it casts no down vote.
        self.click_command(page, '#post-%d-upvote' % self.answer.id)
        self.click_command(page, '#post-%d-downvote' % self.answer.id)
        self.assertEqual(self.shown_score(page, self.answer), '0')
        self.assertEqual(page.locator('#post-%d-downvote.on, #post-%d-upvote.on' % (
            self.answer.id, self.answer.id)).count(), 0)
        self.assertEqual((self.score(self.answer), self.rep(self.bob), self.rep(self.carol)), (0, 100, 500))
        self.assertFalse(VoteUpAction.objects.filter(user=self.carol, canceled=False).exists())
        self.assertFalse(VoteDownAction.objects.filter(user=self.carol, canceled=False).exists())

    def test_daily_votes(self):
        self.set_setting(settings.MAX_VOTES_PER_DAY, 2)
        self.set_setting(settings.USER_REPUTATION_TO_MAX_VOTES, False)
        self.set_setting(settings.START_WARN_VOTES_LEFT, 10)
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, '#post-%d-upvote' % self.question.id, 'You have 1 vote left today.')
        self.click_and_expect(page, '#post-%d-upvote' % self.answer.id, 'You have 0 votes left today.')

        other = self.create_question(self.bob, 'Which projection does SEPAL use for exports?', tags='sepal')
        self.visit(page, other.get_absolute_url())
        self.click_and_expect(page, '#post-%d-upvote' % other.id, "you don't have enough votes left for today")
        self.assertEqual(self.score(other), 0)

        # A vote older than DENY_UNVOTE_DAYS can't be taken back.
        self.set_setting(settings.MAX_VOTES_PER_DAY, 30)
        Action.objects.filter(user=self.carol, node=self.answer, action_type='voteup').update(
            action_date=datetime.datetime.now() - datetime.timedelta(days=2))
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, '#post-%d-upvote' % self.answer.id, 'you cannot cancel a vote after 1 day')
        self.assertEqual(self.score(self.answer), 1)

    # -- accepting --------------------------------------------------------

    def test_accept_and_unaccept(self):
        accept = 'a.accept-answer[href="%s"]' % reverse('accept_answer', kwargs={'id': self.answer.id})
        carol_page = self.open(self.carol)
        self.visit(carol_page, self.question.get_absolute_url())
        self.assertEqual(carol_page.locator(accept).count(), 0)   # not her question

        page = self.open(self.alice)
        self.visit(page, self.question.get_absolute_url())
        self.click_command(page, accept)
        self.assertEqual(page.locator('#answer-container-%d.accepted-answer' % self.answer.id).count(), 1)
        self.assertTrue(Answer.objects.get(id=self.answer.id).nis.accepted)
        self.assertEqual(self.rep(self.bob), 100 + int(settings.REP_GAIN_BY_ACCEPTED))
        self.assertEqual(self.rep(self.alice), 100 + int(settings.REP_GAIN_BY_ACCEPTING))

        # A second answer can't be accepted as well.
        second = self.create_answer(self.carol, self.question, 'Or use the Collect API to fetch a CSV file.')
        self.visit(page, self.question.get_absolute_url())
        self.click_and_expect(page, 'a.accept-answer[href="%s"]' % reverse('accept_answer', kwargs={'id': second.id}),
                              'This question already has an accepted answer.')
        self.assertFalse(Answer.objects.get(id=second.id).nis.accepted)

        # Unaccepting gives the points back.
        self.click_command(page, accept)
        self.assertEqual(page.locator('#answer-container-%d.accepted-answer' % self.answer.id).count(), 0)
        self.assertFalse(Answer.objects.get(id=self.answer.id).nis.accepted)
        self.assertEqual((self.rep(self.bob), self.rep(self.alice)), (100, 100))
        self.assertTrue(AcceptAnswerAction.objects.get(node=self.answer).canceled)

    # -- reporting --------------------------------------------------------

    def test_report_refusals_and_thresholds(self):
        self.set_setting(settings.FLAG_COUNT_TO_HIDE_POST, 2)
        self.set_setting(settings.FLAG_COUNT_TO_DELETE_POST, 3)
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.report(page, self.answer, '   ')
        self.expect_message(page, 'Reason is empty')
        self.report(page, self.answer, 'spam')
        self.expect_message(page, 'Thank you for your report')
        self.report(page, self.answer, 'spam again')
        self.expect_message(page, 'You already flagged this post with the following reason: spam')
        self.assertEqual(FlagAction.objects.filter(node=self.answer).count(), 1)
        self.assertEqual(self.rep(self.bob), 100 - int(settings.REP_LOST_BY_FLAGGED))

        # The second report reaches the "hide" count: one extra penalty, the
        # answer stays visible; the third deletes it.
        dave, erin = self.create_user('dave', reputation=100), self.create_user('erin', reputation=100)
        dave_page = self.open(dave)
        self.visit(dave_page, self.question.get_absolute_url())
        self.report(dave_page, self.answer, 'advertising')
        self.expect_message(dave_page, 'Thank you for your report')
        expected = 100 - 2 * int(settings.REP_LOST_BY_FLAGGED) - int(settings.REP_LOST_BY_FLAGGED_3_TIMES)
        self.assertEqual(self.rep(self.bob), expected)
        self.visit(self.open(), self.question.get_absolute_url())
        self.assertFalse(Answer.objects.get(id=self.answer.id).nis.deleted)

        erin_page = self.open(erin)
        self.visit(erin_page, self.question.get_absolute_url())
        self.report(erin_page, self.answer, 'spam')
        self.expect_message(erin_page, 'Thank you for your report')
        expected -= int(settings.REP_LOST_BY_FLAGGED) + int(settings.REP_LOST_BY_FLAGGED_5_TIMES)
        self.assertEqual(self.rep(self.bob), expected)
        self.assertTrue(Answer.objects.get(id=self.answer.id).nis.deleted)
        self.assertEqual(DeleteAction.objects.get(node=self.answer).extra, 'BYFLAGGED')
        anonymous = self.open()
        self.visit(anonymous, self.question.get_absolute_url())
        self.assertEqual(anonymous.locator('#answer-container-%d' % self.answer.id).count(), 0)

    def test_reports_per_day(self):
        self.set_setting(settings.MAX_FLAGS_PER_DAY, 1)
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.report(page, self.question, 'off topic')
        self.expect_message(page, 'Thank you for your report')
        self.report(page, self.answer, 'spam')
        self.expect_message(page, "you don't have enough flags left for today")
        self.assertFalse(FlagAction.objects.filter(node=self.answer).exists())

    # -- comments ---------------------------------------------------------

    def test_comment_rules(self):
        comment = self.create_comment(self.carol, self.question, 'Which version of Collect do you use?')

        # A newcomer can't comment on someone else's post, nor like comments.
        newbie = self.create_user('newbie', reputation=1)
        page = self.open(newbie)
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('#comment-tools-%d .add-comment-link' % self.question.id).count(), 0)
        self.assertEqual(page.locator('#post-%d-upvote' % comment.id).count(), 0)
        result = page.evaluate('''url => fetch(url, {method: 'POST',
            body: new URLSearchParams({comment: 'Ten chars or more here'}),
            headers: {'X-CSRFToken': document.querySelector('meta[name=csrf-token]').content,
                      'X-Requested-With': 'XMLHttpRequest'}}).then(r => r.json())''',
                               reverse('comment', kwargs={'id': self.question.id}))
        self.assertFalse(result['success'])
        self.assertIn("you don't have enough reputation points to comment", result['error_message'])
        self.assertEqual(Comment.objects.filter(parent=self.question).count(), 1)

        # carol: the form only sends comments of the right length; the server
        # says why it refuses one that gets through anyway.
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('#post-%d-upvote' % comment.id).count(), 0)   # her own comment
        page.click('#comment-tools-%d .add-comment-link' % self.question.id)
        form = page.locator('#comment-%d-form' % self.question.id)
        submit = form.locator('input[type=submit]').first
        counter = page.locator('#comment-%d-chars-left .comments-char-left-count' % self.question.id)
        form.locator('textarea').fill('Too short')
        page.wait_for_function('el => el.textContent.trim() === "1"', arg=counter.element_handle())
        self.assertTrue(submit.is_disabled())
        submit.evaluate('el => el.disabled = false')
        submit.click()
        self.expect_message(page, 'At least 10 characters required on comment body.')
        # the form stays open with her text, so she can fix it
        self.assertTrue(form.is_visible())
        self.assertEqual(form.locator('textarea').input_value(), 'Too short')
        form.locator('textarea').fill('x' * 601)
        page.wait_for_function('el => el.textContent.trim() === "-1"', arg=counter.element_handle())
        self.assertTrue(submit.is_disabled())
        submit.evaluate('el => el.disabled = false')
        submit.click()
        self.expect_message(page, 'No more than 600 characters on comment body.')
        self.assertEqual(Comment.objects.filter(parent=self.question).count(), 1)

        # After an hour her comment can no longer be edited.
        self.assertEqual(page.locator('#comment-%d-edit' % comment.id).count(), 1)
        Comment.objects.filter(id=comment.id).update(added_at=datetime.datetime.now() - datetime.timedelta(minutes=61))
        from django.core.cache import cache
        cache.clear()
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('#comment-%d-edit' % comment.id).count(), 0)

    def test_moderator_deletes_a_comment(self):
        comment = self.create_comment(self.carol, self.question, 'Buy cheap watches at example.com')
        bob_page = self.open(self.bob)
        self.visit(bob_page, self.question.get_absolute_url())
        self.assertEqual(bob_page.locator('#comment-%d-delete' % comment.id).count(), 0)

        moderator = self.create_user('moderator', is_staff=True)
        page = self.open(moderator)
        self.visit(page, self.question.get_absolute_url())
        page.click('#comment-%d-delete' % comment.id)
        self.confirm_dialog(page)
        page.wait_for_selector('#comment-%d' % comment.id, state='detached')
        self.assertTrue(Comment.objects.get(id=comment.id).nis.deleted)
        self.assertEqual(DeleteAction.objects.get(node=comment).user, moderator)
        self.visit(bob_page, self.question.get_absolute_url())
        self.assertNotIn('cheap watches', bob_page.content())

    # -- awarding points --------------------------------------------------

    def test_award_points_refusals(self):
        award = '#answer-container-%d a[href="%s"]' % (self.answer.id, reverse(
            'award_points', kwargs={'user_id': self.bob.id, 'answer_id': self.answer.id}))
        bob_page = self.open(self.bob)
        self.visit(bob_page, self.question.get_absolute_url())
        self.assertEqual(bob_page.locator(award).count(), 0)   # not on his own answer

        page = self.open(self.alice)
        self.visit(page, self.question.get_absolute_url())
        page.click(award)
        page.wait_for_selector('.dialog.prompt #points_input')
        page.fill('#points_input', '0')
        page.click('.dialog.prompt .dialog-yes')
        self.expect_message(page, 'The number of points to award needs to be a positive value.')
        self.assertEqual((self.rep(self.alice), self.rep(self.bob)), (100, 100))

    # -- retagging --------------------------------------------------------

    def test_retag_without_edit_rights(self):
        self.set_setting(settings.REP_TO_RETAG, 500)
        self.set_setting(settings.REP_TO_EDIT_OTHERS, 2000)
        body = Question.objects.get(id=self.question.id).body
        page = self.open(self.carol)
        self.visit(page, self.question.get_absolute_url())
        # no edit link ("suggest an edit" is offered instead)
        self.assertEqual(page.locator('.action-link a').filter(has_text=re.compile(r'^\s*edit\s*$')).count(), 0)
        with page.expect_navigation():
            page.locator('.action-link a').filter(has_text='retag').first.click()
        self.check_page(page)
        self.assertEqual(page.locator('#editor').count(), 0)   # tags only
        page.fill('#id_tags', 'collect csv')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.click('#fmretag input[type=submit]')
        self.check_page(page)
        question = Question.objects.get(id=self.question.id)
        self.assertEqual(sorted(question.tagname_list()), ['collect', 'csv'])
        self.assertEqual(question.body, body)
        self.assertEqual(question.title, 'How do I export Collect survey data to CSV?')
        self.assertTrue(RetagAction.objects.filter(user=self.carol, node=question).exists())
        self.assertIn('/tags/csv/', page.content())
