"""The rules of voting, accepting, flagging, commenting, awarding points and
reputation, through the command views (the browser side is e2e/test_rules.py)."""
import datetime

from django.core.cache import cache
from django.urls import reverse

import forum
from forum import settings
from forum.actions import AnswerAction, AskAction, CommentAction
from forum.actions.meta import DeleteAction, FlagAction, VoteDownAction, VoteUpAction
from forum.actions.node import RetagAction
from forum.models import Answer, Comment, Question, User
from forum.models.action import Action

from .base import OSQATestCase


class RulesTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100, email_isvalid=True)   # asks
        self.bob = self.create_user('bob', reputation=100, email_isvalid=True)       # answers
        self.carol = self.create_user('carol', reputation=500, email_isvalid=True)   # votes, flags
        self.question = self.ask(self.alice, 'How do I export Collect data to CSV?')

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def _as_request(self, user):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory
        request = RequestFactory().post('/')
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        forum.REQUEST_HOLDER.request = request
        self.addCleanup(setattr, forum.REQUEST_HOLDER, 'request', None)

    def ask(self, user, title, tags='collect'):
        self._as_request(user)
        action = AskAction(user=user, ip='127.0.0.1').save(data=dict(
            title=title, tags=tags, text='Details of the question, long enough to be valid.'))
        return Question.objects.get(id=action.node.id)

    def answer(self, user, question, text='An answer with enough text to be posted.'):
        self._as_request(user)
        return Answer.objects.get(id=AnswerAction(user=user, ip='127.0.0.1').save(
            data=dict(question=question, text=text)).node.id)

    def add_comment(self, user, post, text='A comment that is long enough.'):
        self._as_request(user)
        return Comment.objects.get(id=CommentAction(user=user, ip='127.0.0.1').save(
            data=dict(text=text, parent=post)).node.id)

    def rep(self, user):
        return User.objects.filter(id=user.id).values_list('reputation', flat=True)[0]

    def score(self, node):
        return type(node).objects.filter(id=node.id).values_list('score', flat=True)[0]

    def as_user(self, user):
        self.client.logout()
        self.login(user)

    def vote(self, node, kind='up'):
        return self.command(reverse('vote_post', kwargs={'id': node.id, 'vote_type': kind}))

    def refused(self, result, text):
        self.assertFalse(result['success'], result)
        self.assertIn(text, result['error_message'])


class VotingTest(RulesTestCase):
    def test_anonymous_is_asked_to_sign_in(self):
        result = self.vote(self.question)
        self.refused(result, 'anonymous users cannot vote')
        self.assertIn(reverse('auth_signin'), result['error_message'])

    def test_not_enough_reputation(self):
        newbie = self.create_user('newbie', reputation=1)
        self.as_user(newbie)
        self.refused(self.vote(self.question), "you don't have enough reputation points to upvote")
        self.refused(self.vote(self.question, 'down'), "you don't have enough reputation points to downvote")
        self.assertEqual(self.score(self.question), 0)

    def test_each_direction_has_its_own_minimum(self):
        # Down votes allowed from 5, up votes only from 50: a user with 10 can
        # vote down but not up (the old check let "can vote down" through for up).
        self.set(settings.REP_TO_VOTE_DOWN, 5)
        self.set(settings.REP_TO_VOTE_UP, 50)
        dave = self.create_user('dave', reputation=10)
        self.as_user(dave)
        self.refused(self.vote(self.question), 'The minimum reputation required is 50 (yours is 10)')
        self.assertEqual(self.score(self.question), 0)
        self.assertTrue(self.vote(self.question, 'down')['success'])
        self.assertEqual(self.score(self.question), -1)

    def test_down_vote_costs_author_and_voter(self):
        self.as_user(self.carol)
        self.assertTrue(self.vote(self.question, 'down')['success'])
        self.assertEqual(self.score(self.question), -1)
        self.assertEqual(self.rep(self.alice), 100 - int(settings.REP_LOST_BY_DOWNVOTED))   # -2
        self.assertEqual(self.rep(self.carol), 500 - int(settings.REP_LOST_BY_DOWNVOTING))  # -1
        self.assertTrue(VoteDownAction.objects.filter(user=self.carol, node=self.question, canceled=False).exists())

    def test_down_after_up_cancels_the_up_vote(self):
        """Any click on a post you voted on takes the vote back; it doesn't
        turn an up vote into a down vote."""
        self.as_user(self.carol)
        self.vote(self.question, 'up')
        result = self.vote(self.question, 'down')
        self.assertTrue(result['success'])
        self.assertEqual(result['commands']['update_user_post_vote'], [str(self.question.id), 'none'])
        self.assertEqual(self.score(self.question), 0)
        self.assertEqual(self.rep(self.alice), 100)
        self.assertEqual(self.rep(self.carol), 500)
        self.assertTrue(VoteUpAction.objects.get(user=self.carol, node=self.question).canceled)
        self.assertFalse(VoteDownAction.objects.filter(user=self.carol).exists())

    def test_votes_per_day(self):
        self.set(settings.MAX_VOTES_PER_DAY, 1)
        self.set(settings.USER_REPUTATION_TO_MAX_VOTES, False)
        other = self.ask(self.bob, 'Another question about Collect exports')
        self.as_user(self.carol)
        self.assertTrue(self.vote(self.question)['success'])
        self.refused(self.vote(other), "you don't have enough votes left for today")
        self.assertEqual(self.score(other), 0)
        # at the limit she can still take her vote back
        self.assertTrue(self.vote(self.question)['success'])
        self.assertEqual(self.score(self.question), 0)

    def test_votes_left_warning(self):
        self.set(settings.MAX_VOTES_PER_DAY, 5)
        self.set(settings.USER_REPUTATION_TO_MAX_VOTES, False)
        self.set(settings.START_WARN_VOTES_LEFT, 100)
        other = self.ask(self.bob, 'Another question about Collect exports')
        self.as_user(self.carol)
        self.assertEqual(self.vote(self.question)['message'], 'You have 4 votes left today.')
        self.assertEqual(self.vote(other)['message'], 'You have 3 votes left today.')
        self.assertEqual(self.vote(other)['message'], 'You have 4 votes left today.')   # cancelled

    def test_no_warning_with_many_votes_left(self):
        self.set(settings.START_WARN_VOTES_LEFT, 10)
        self.as_user(self.carol)
        self.assertNotIn('message', self.vote(self.question))

    def test_old_vote_cannot_be_cancelled(self):
        self.as_user(self.carol)
        self.vote(self.question)
        Action.objects.filter(user=self.carol, action_type='voteup').update(
            action_date=datetime.datetime.now() - datetime.timedelta(days=int(settings.DENY_UNVOTE_DAYS) + 1))
        self.refused(self.vote(self.question), 'you cannot cancel a vote after 1 day')
        self.assertEqual(self.score(self.question), 1)

    def test_a_day_is_the_last_24_hours(self):
        self.as_user(self.carol)
        self.vote(self.question)
        votes = Action.objects.filter(user=self.carol, action_type='voteup')
        self.assertEqual(self.carol.get_vote_count_today(), 1)
        votes.update(action_date=datetime.datetime.now() - datetime.timedelta(hours=23))
        self.assertEqual(self.carol.get_vote_count_today(), 1)
        # Yesterday just after midnight: more than 24 hours ago (the old count
        # started at midnight yesterday, so a "day" could last 48 hours).
        yesterday = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=1),
                                              datetime.time(0, 0, 1))
        votes.update(action_date=yesterday)
        self.assertEqual(self.carol.get_vote_count_today(), 0)

    def test_flags_per_day_window(self):
        self.as_user(self.carol)
        self.command(reverse('flag_post', kwargs={'id': self.question.id}), {'prompt': 'spam'})
        self.assertEqual(self.carol.get_flagged_items_count_today(), 1)
        Action.objects.filter(user=self.carol, action_type='flag').update(
            action_date=datetime.datetime.now() - datetime.timedelta(hours=25))
        self.assertEqual(self.carol.get_flagged_items_count_today(), 0)


class AcceptingTest(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.answer1 = self.answer(self.bob, self.question)

    def accept(self, answer):
        return self.command(reverse('accept_answer', kwargs={'id': answer.id}))

    def accepted(self, answer):
        return Answer.objects.filter(id=answer.id).values_list('marked', flat=True)[0]

    def test_only_the_asker_accepts(self):
        self.as_user(self.carol)
        self.refused(self.accept(self.answer1), 'you cannot accept the answer')
        self.assertFalse(self.accepted(self.answer1))

    def test_accepting_gives_reputation_and_unaccepting_takes_it_back(self):
        self.as_user(self.alice)
        self.assertTrue(self.accept(self.answer1)['success'])
        self.assertTrue(self.accepted(self.answer1))
        self.assertEqual(self.rep(self.bob), 100 + int(settings.REP_GAIN_BY_ACCEPTED))     # +15
        self.assertEqual(self.rep(self.alice), 100 + int(settings.REP_GAIN_BY_ACCEPTING))  # +2
        self.assertEqual(self.accept(self.answer1)['commands'], {'unmark_accepted': [self.answer1.id]})
        self.assertFalse(self.accepted(self.answer1))
        self.assertEqual((self.rep(self.bob), self.rep(self.alice)), (100, 100))

    def test_one_accepted_answer(self):
        answer2 = self.answer(self.carol, self.question, 'Another answer with enough text to be posted.')
        self.as_user(self.alice)
        self.accept(self.answer1)
        self.refused(self.accept(answer2), 'This question already has an accepted answer.')
        self.assertFalse(self.accepted(answer2))

    def test_accepted_answers_per_author(self):
        self.set(settings.MAXIMUM_ACCEPTED_ANSWERS, 3)
        self.set(settings.MAXIMUM_ACCEPTED_PER_USER, 1)
        answer2 = self.answer(self.bob, self.question, 'A second answer by bob, long enough to post.')
        answer3 = self.answer(self.carol, self.question, 'An answer by carol, long enough to post.')
        self.as_user(self.alice)
        self.accept(self.answer1)
        self.refused(self.accept(answer2), 'The author of this answer already has an accepted answer in this question.')
        self.assertTrue(self.accept(answer3)['success'])
        self.assertEqual(Question.objects.get(id=self.question.id).accepted_count, 2)

    def test_accepting_switched_off(self):
        self.set(settings.DISABLE_ACCEPTING_FEATURE, True)
        self.as_user(self.alice)
        response = self.client.post(reverse('accept_answer', kwargs={'id': self.answer1.id}), **self.AJAX)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.accepted(self.answer1))

    def test_accepting_own_answer(self):
        own = self.answer(self.alice, self.question, 'I found it myself: use the CSV export.')
        self.as_user(self.alice)
        self.refused(self.accept(own), 'you cannot accept the answer')
        self.set(settings.USERS_CAN_ACCEPT_OWN, True)
        self.assertTrue(self.accept(own)['success'])
        self.assertTrue(self.accepted(own))
        self.assertEqual(self.rep(self.alice), 100)   # no reputation for accepting yourself

    def test_plain_post_redirects_with_message(self):
        self.as_user(self.alice)
        response = self.client.post(reverse('accept_answer', kwargs={'id': self.answer1.id}))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(self.question.get_absolute_url() + '?accepted_answer=true'))
        self.assertTrue(self.accepted(self.answer1))
        self.assertContains(self.client.get(response['Location']), "Congratulations! You've accepted an answer.")


class FlaggingTest(RulesTestCase):
    def flag(self, node, reason='spam'):
        return self.command(reverse('flag_post', kwargs={'id': node.id}), {'prompt': reason})

    def test_refusals(self):
        self.as_user(self.alice)
        self.refused(self.flag(self.question), 'you cannot flag your own post')

        newbie = self.create_user('newbie', reputation=1)
        self.as_user(newbie)
        self.refused(self.flag(self.question), "you don't have enough reputation points to flag posts")

        self.as_user(self.carol)
        self.refused(self.flag(self.question, '   '), 'Reason is empty')
        self.assertTrue(self.flag(self.question)['success'])
        self.refused(self.flag(self.question, 'again'), 'You already flagged this post with the following reason: spam')
        self.assertEqual(FlagAction.objects.filter(node=self.question).count(), 1)

    def test_flags_per_day(self):
        self.set(settings.MAX_FLAGS_PER_DAY, 1)
        other = self.ask(self.bob, 'Another question about Collect exports')
        self.as_user(self.carol)
        self.assertTrue(self.flag(self.question)['success'])
        self.refused(self.flag(other), "you don't have enough flags left for today")
        self.assertFalse(FlagAction.objects.filter(node=other).exists())

    def test_thresholds_cost_the_author_once(self):
        self.set(settings.FLAG_COUNT_TO_HIDE_POST, 3)
        self.set(settings.FLAG_COUNT_TO_DELETE_POST, 5)
        self.set(settings.MAX_FLAGS_PER_DAY, 10)
        flaggers = [self.create_user('flagger%d' % i, reputation=100) for i in range(6)]
        per_flag, hide, delete = (int(settings.REP_LOST_BY_FLAGGED), int(settings.REP_LOST_BY_FLAGGED_3_TIMES),
                                  int(settings.REP_LOST_BY_FLAGGED_5_TIMES))
        expected = 100
        for count, flagger in enumerate(flaggers, 1):
            self.as_user(flagger)
            self.assertTrue(self.flag(self.question)['success'])
            expected -= per_flag + (hide if count == 3 else 0) + (delete if count == 5 else 0)
            self.assertEqual(self.rep(self.alice), expected, 'after %d flags' % count)
            deleted = Question.objects.get(id=self.question.id).nis.deleted
            self.assertEqual(deleted, count >= 5, 'after %d flags' % count)
        deletes = DeleteAction.objects.filter(node=self.question)
        self.assertEqual([d.extra for d in deletes], ['BYFLAGGED'])

    def test_flagged_post_is_not_hidden(self):
        """FLAG_COUNT_TO_HIDE_POST only costs reputation (see its description)."""
        self.set(settings.FLAG_COUNT_TO_HIDE_POST, 1)
        self.as_user(self.carol)
        self.flag(self.question)
        self.client.logout()
        self.assertContains(self.client.get(self.question.get_absolute_url()), 'How do I export Collect data to CSV?')
        self.assertContains(self.client.get('/questions/'), 'How do I export Collect data to CSV?')


class CommentRulesTest(RulesTestCase):
    def post_comment(self, node, text, **extra):
        return self.command(reverse('comment', kwargs={'id': node.id}), dict(comment=text, **extra))

    def test_length(self):
        self.as_user(self.carol)
        self.refused(self.post_comment(self.question, '   '), 'Comment is empty')
        self.refused(self.post_comment(self.question, 'Too short'), 'At least 10 characters required on comment body.')
        self.refused(self.post_comment(self.question, 'x' * 601), 'No more than 600 characters on comment body.')
        self.assertTrue(self.post_comment(self.question, 'x' * 600)['success'])
        self.assertEqual(Comment.objects.filter(parent=self.question).count(), 1)

    def test_low_reputation_comments_only_on_own_posts(self):
        newbie = self.create_user('newbie', reputation=1, email_isvalid=True)
        own = self.ask(newbie, 'A question by a newcomer about Collect')
        self.as_user(newbie)
        self.refused(self.post_comment(self.question, 'A comment that is long enough.'),
                     "you don't have enough reputation points to comment")
        self.assertTrue(self.post_comment(own, 'A comment that is long enough.')['success'])

    def test_edit_window(self):
        comment = self.add_comment(self.carol, self.question)
        self.as_user(self.carol)
        self.assertTrue(self.post_comment(self.question, 'An edited comment, long enough.', id=comment.id)['success'])
        Comment.objects.filter(id=comment.id).update(added_at=datetime.datetime.now() - datetime.timedelta(minutes=61))
        cache.clear()   # the comment is cached by id
        self.refused(self.post_comment(self.question, 'Edited again an hour later.', id=comment.id),
                     'a comment can only be edited during the first 60 minutes')
        self.assertIn('An edited comment', Comment.objects.get(id=comment.id).comment)
        # someone else can't edit it at all
        self.as_user(self.bob)
        self.refused(self.post_comment(self.question, 'Edited by somebody else.', id=comment.id),
                     "you don't have enough reputation points to edit comments")

    def test_like_refusals(self):
        comment = self.add_comment(self.carol, self.question)
        url = reverse('like_comment', kwargs={'id': comment.id})
        self.as_user(self.carol)
        self.refused(self.command(url), 'you cannot like your own post')
        newbie = self.create_user('newbie', reputation=1)
        self.as_user(newbie)
        self.refused(self.command(url), "you don't have enough reputation points to like comments")
        self.assertEqual(self.score(comment), 0)

    def test_moderator_deletes_a_comment(self):
        comment = self.add_comment(self.carol, self.question)
        url = reverse('delete_comment', kwargs={'id': comment.id})
        self.as_user(self.bob)
        self.refused(self.command(url), "you don't have enough reputation points to delete comments")
        moderator = self.create_user('mod', is_staff=True)
        self.as_user(moderator)
        self.assertEqual(self.command(url)['commands'], {'remove_comment': [comment.id]})
        self.assertTrue(Comment.objects.get(id=comment.id).nis.deleted)


class AwardPointsTest(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.answer1 = self.answer(self.bob, self.question)

    def award(self, user, points):
        return self.command(reverse('award_points', kwargs={'user_id': user.id, 'answer_id': self.answer1.id}),
                            {'points': points, 'message': 'thanks'})

    def test_refusals(self):
        self.as_user(self.bob)
        self.refused(self.award(self.bob, 10), 'you cannot award your own post')
        self.as_user(self.alice)
        for points in ('0', '-5', 'many'):
            self.refused(self.award(self.bob, points), 'The number of points to award needs to be a positive value.')
        self.refused(self.award(self.bob, 101), "you don't have enough reputation points to award")
        self.assertEqual((self.rep(self.alice), self.rep(self.bob)), (100, 100))
        self.assertTrue(self.award(self.bob, 100)['success'])
        self.assertEqual((self.rep(self.alice), self.rep(self.bob)), (0, 200))


class RetagTest(RulesTestCase):
    def test_retag_without_edit_rights(self):
        self.set(settings.REP_TO_RETAG, 500)
        self.set(settings.REP_TO_EDIT_OTHERS, 2000)
        body = self.question.body
        self.as_user(self.carol)   # 500
        url = reverse('edit_question', kwargs={'id': self.question.id})
        page = self.client.get(url)
        self.assertTemplateUsed(page, 'question_retag.html')
        self.assertEqual(self.client.post(url, {'tags': 'collect csv'}).status_code, 302)
        question = Question.objects.get(id=self.question.id)
        self.assertEqual(sorted(question.tagname_list()), ['collect', 'csv'])
        self.assertEqual(question.body, body)
        self.assertEqual(question.title, 'How do I export Collect data to CSV?')
        self.assertTrue(RetagAction.objects.filter(user=self.carol, node=question).exists())
        # nothing changed: no new action
        self.client.post(url, {'tags': 'collect csv'})
        self.assertEqual(RetagAction.objects.filter(node=question).count(), 1)

        self.as_user(self.bob)   # 100: neither
        self.assertEqual(self.client.get(url).status_code, 404)


class ReputationRulesTest(RulesTestCase):
    def test_wiki_posts_give_no_reputation(self):
        self.question.nstate.wiki = AskAction.objects.get(node=self.question)
        self.as_user(self.carol)
        self.assertTrue(self.vote(self.question)['success'])
        self.assertEqual(self.score(self.question), 1)
        self.assertEqual(self.rep(self.alice), 100)

    def test_initial_reputation(self):
        self.set(settings.INITIAL_REP, 7)
        response = self.client.post('/account/local/register/', {
            'username': 'newcomer', 'email': 'newcomer@example.com', 'password1': 's3cret-pass-1',
            'password2': 's3cret-pass-1', 'next': '/', 'form_token': self.signup_token()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username='newcomer').reputation, 7)

    def test_daily_cap_on_reputation_from_up_votes(self):
        self.set(settings.REP_GAIN_BY_UPVOTED, 10)
        self.set(settings.MAX_REP_BY_UPVOTE_DAY, 25)
        voters = [self.create_user('voter%d' % i, reputation=100) for i in range(5)]

        def vote(voter):
            self.as_user(voter)
            self.assertTrue(self.vote(self.question)['success'])

        vote(voters[0])
        vote(voters[1])
        self.assertEqual(self.rep(self.alice), 120)
        vote(voters[2])
        self.assertEqual(self.rep(self.alice), 125)   # only 5 left under the cap
        vote(voters[3])
        self.assertEqual(self.rep(self.alice), 125)   # past the cap: score only
        self.assertEqual(self.score(self.question), 4)

        # Cancelling a vote takes back only what it gave.
        vote(voters[3])
        self.assertEqual(self.rep(self.alice), 125)
        vote(voters[2])
        self.assertEqual(self.rep(self.alice), 120)
        # ...which makes room again under the cap
        vote(voters[4])
        self.assertEqual(self.rep(self.alice), 125)
        self.assertEqual(self.alice.get_reputation_by_upvoted_today(), 25)

        # Up votes older than 24 hours don't count towards today's cap.
        Action.objects.filter(action_type='voteup').update(
            action_date=datetime.datetime.now() - datetime.timedelta(hours=25))
        self.assertEqual(self.alice.get_reputation_by_upvoted_today(), 0)
        vote(voters[2])
        self.assertEqual(self.rep(self.alice), 135)

    def test_cap_zero_means_no_limit(self):
        self.set(settings.MAX_REP_BY_UPVOTE_DAY, 0)
        self.set(settings.REP_GAIN_BY_UPVOTED, 10)
        for i in range(3):
            self.as_user(self.create_user('voter%d' % i, reputation=100))
            self.vote(self.question)
        self.assertEqual(self.rep(self.alice), 130)

    def test_comment_likes_give_no_reputation(self):
        comment = self.add_comment(self.bob, self.question)
        self.as_user(self.carol)
        self.command(reverse('like_comment', kwargs={'id': comment.id}))
        self.assertEqual(self.score(comment), 1)
        self.assertEqual(self.rep(self.bob), 100)


class AnswerPostingLimitTest(RulesTestCase):
    def post_answer(self, text):
        return self.client.post(reverse('answer', kwargs={'id': self.question.id}), {'text': text})

    def test_answers_per_hour(self):
        self.set(settings.ANSWERS_PER_HOUR, 1)
        self.as_user(self.bob)
        self.assertEqual(self.post_answer('A first answer that is long enough to post.').status_code, 302)
        response = self.post_answer('A second answer that is long enough to post.')
        self.assertTrue(response['Location'].endswith('#fmanswer'))
        page = self.client.get(response['Location'])
        self.assertContains(page, 'You have posted the maximum number of answers for one hour.')
        self.assertContains(page, 'A second answer that is long enough')   # kept in the form
        self.assertEqual(Answer.objects.filter(parent=self.question).count(), 1)

        moderator = self.create_user('mod', is_staff=True, email_isvalid=True)
        self.as_user(moderator)
        self.post_answer('A moderator answer that is long enough.')
        self.post_answer('Another moderator answer, long enough.')
        self.assertEqual(Answer.objects.filter(parent=self.question, author=moderator).count(), 2)


class ReputationFloorTest(RulesTestCase):
    def test_cancelling_gives_back_only_what_was_taken(self):
        # Without negative reputation alice stops at 0; when carol takes her
        # down vote back, alice gets back what she lost, not the full 2.
        self.set(settings.ALLOW_NEGATIVE_REPUTATION, False)
        User.objects.filter(id=self.alice.id).update(reputation=1)
        self.as_user(self.carol)
        self.assertTrue(self.vote(self.question, 'down')['success'])
        self.assertEqual(self.rep(self.alice), 0)
        self.assertTrue(self.vote(self.question, 'down')['success'])   # cancelled
        self.assertEqual(self.rep(self.alice), 1)

    def test_gains_are_not_clamped(self):
        self.set(settings.ALLOW_NEGATIVE_REPUTATION, False)
        self.as_user(self.carol)
        self.vote(self.question)
        self.assertEqual(self.rep(self.alice), 100 + int(settings.REP_GAIN_BY_UPVOTED))
