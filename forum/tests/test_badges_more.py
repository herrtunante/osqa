"""Each default badge, triggered through the forum's actions: awarded once,
and not again when the same thing happens a second time."""
import datetime

from forum.actions import (AcceptAnswerAction, AnswerAction, AskAction, CommentAction, DeleteAction,
                           EditProfileAction, EmailValidationAction, FavoriteAction, QuestionViewAction,
                           RetagAction, ReviseAction, RollbackAction, VoteDownAction, VoteUpAction)
from forum.models import Answer, Award, Badge, Question
from forum.models.action import Action
from forum_modules.default_badges import settings as badge_settings

from .base import OSQATestCase

IP = '127.0.0.1'


class BadgeTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)
        self.voters = [self.create_user('voter%d' % n, reputation=1000) for n in range(4)]
        # Asking and answering show a message to the current request's user.
        import forum
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory
        request = RequestFactory().post('/')
        request.user, request.session = self.alice, SessionStore()
        request._messages = FallbackStorage(request)
        forum.REQUEST_HOLDER.request = request
        self.addCleanup(setattr, forum.REQUEST_HOLDER, 'request', None)

    def set_setting(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def awards(self, user, badge):
        return Award.objects.filter(user=user, badge__cls=badge).count()

    def ask(self, user=None, title='How do I export survey data to CSV?', tags='csv'):
        return Question.objects.get(id=AskAction(user=user or self.alice, ip=IP).save(data=dict(
            title=title, tags=tags, text='A question body that is long enough to be posted.')).node.id)

    def answer(self, question, user):
        return Answer.objects.get(id=AnswerAction(user=user, ip=IP).save(data=dict(
            question=question, text='An answer with enough text to be posted.')).node.id)

    def vote_up(self, node, voters):
        return [VoteUpAction(user=voter, node=node, ip=IP).save() for voter in voters]

    def revise(self, question, user, text):
        return ReviseAction(user=user, node=question, ip=IP).save(data=dict(
            title=question.title, tags=question.tagnames, text=text, summary='edit'))


class QuestionViewBadgesTest(BadgeTestCase):
    def test_popular_notable_and_famous_question(self):
        self.set_setting(badge_settings.POPULAR_QUESTION_VIEWS, 2)
        self.set_setting(badge_settings.NOTABLE_QUESTION_VIEWS, 3)
        self.set_setting(badge_settings.FAMOUS_QUESTION_VIEWS, 4)
        question = self.ask()
        viewer = self.voters[0]

        QuestionViewAction(question, viewer, ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'PopularQuestion'), 0)
        for _ in range(5):
            QuestionViewAction(Question.objects.get(id=question.id), viewer, ip=IP).save()
        self.assertEqual(Question.objects.get(id=question.id).extra_count, 6)
        for badge in ('PopularQuestion', 'NotableQuestion', 'FamousQuestion'):
            self.assertEqual(self.awards(self.alice, badge), 1, badge)


class ScoreBadgesTest(BadgeTestCase):
    def test_nice_good_and_great_question(self):
        self.set_setting(badge_settings.NICE_QUESTION_VOTES_UP, 1)
        self.set_setting(badge_settings.GOOD_QUESTION_VOTES_UP, 2)
        self.set_setting(badge_settings.GREAT_QUESTION_VOTES_UP, 3)
        question = self.ask()
        votes = self.vote_up(question, self.voters[:3])
        for badge in ('NiceQuestion', 'GoodQuestion', 'GreatQuestion'):
            self.assertEqual(self.awards(self.alice, badge), 1, badge)

        # The score drops and comes back: no second award.
        votes[2].cancel()
        self.vote_up(question, self.voters[2:3])
        self.assertEqual(Question.objects.get(id=question.id).score, 3)
        self.assertEqual(self.awards(self.alice, 'GreatQuestion'), 1)
        # Answer badges are for answers.
        self.assertEqual(self.awards(self.alice, 'NiceAnswer'), 0)

    def test_nice_good_and_great_answer(self):
        self.set_setting(badge_settings.NICE_ANSWER_VOTES_UP, 1)
        self.set_setting(badge_settings.GOOD_ANSWER_VOTES_UP, 2)
        self.set_setting(badge_settings.GREAT_ANSWER_VOTES_UP, 3)
        bob = self.create_user('bob')
        answer = self.answer(self.ask(), bob)
        votes = self.vote_up(answer, self.voters[:3])
        for badge in ('NiceAnswer', 'GoodAnswer', 'GreatAnswer'):
            self.assertEqual(self.awards(bob, badge), 1, badge)
        votes[0].cancel()
        self.vote_up(answer, self.voters[3:])
        self.assertEqual(self.awards(bob, 'GreatAnswer'), 1)
        self.assertEqual(self.awards(bob, 'NiceQuestion'), 0)

    def test_self_learner(self):
        self.set_setting(badge_settings.SELF_LEARNER_UP_VOTES, 2)
        question = self.ask()
        own = self.answer(question, self.alice)
        other = self.answer(question, self.create_user('bob'))
        self.vote_up(other, self.voters[:2])
        self.assertEqual(self.awards(self.alice, 'SelfLearner'), 0)
        self.vote_up(own, self.voters[:1])
        self.assertEqual(self.awards(self.alice, 'SelfLearner'), 0)
        votes = self.vote_up(own, self.voters[1:2])
        self.assertEqual(self.awards(self.alice, 'SelfLearner'), 1)
        votes[0].cancel()
        self.vote_up(own, self.voters[1:2])
        self.assertEqual(self.awards(self.alice, 'SelfLearner'), 1)

    def test_necromancer(self):
        self.set_setting(badge_settings.NECROMANCER_DIF_DAYS, 30)
        self.set_setting(badge_settings.NECROMANCER_UP_VOTES, 2)
        bob = self.create_user('bob')
        old = self.ask()
        Question.objects.filter(id=old.id).update(added_at=datetime.datetime.now() - datetime.timedelta(days=40))
        new = self.ask(title='A recent question about CSV')
        late = self.answer(Question.objects.get(id=old.id), bob)
        prompt = self.answer(new, bob)
        self.vote_up(prompt, self.voters[:2])
        self.assertEqual(self.awards(bob, 'Necromancer'), 0)
        self.vote_up(late, self.voters[:2])
        self.assertEqual(self.awards(bob, 'Necromancer'), 1)
        self.vote_up(late, self.voters[2:3])
        self.assertEqual(self.awards(bob, 'Necromancer'), 1)


class AcceptedAnswerBadgesTest(BadgeTestCase):
    def test_enlightened_and_guru(self):
        self.set_setting(badge_settings.ENLIGHTENED_UP_VOTES, 1)
        self.set_setting(badge_settings.GURU_UP_VOTES, 2)
        bob = self.create_user('bob')
        question = self.ask()
        answer = self.answer(question, bob)
        self.vote_up(answer, self.voters[:1])
        self.assertEqual(self.awards(bob, 'Enlightened'), 0)  # not accepted yet
        AcceptAnswerAction(user=self.alice, node=answer, ip=IP).save()
        self.assertEqual(self.awards(bob, 'Enlightened'), 1)
        self.assertEqual(self.awards(bob, 'Guru'), 0)
        self.vote_up(Answer.objects.get(id=answer.id), self.voters[1:3])
        self.assertEqual(self.awards(bob, 'Enlightened'), 1)
        self.assertEqual(self.awards(bob, 'Guru'), 1)


class FavoriteBadgesTest(BadgeTestCase):
    def test_favorite_and_stellar_question(self):
        self.set_setting(badge_settings.FAVORITE_QUESTION_FAVS, 1)
        self.set_setting(badge_settings.STELLAR_QUESTION_FAVS, 2)
        question = self.ask()
        first = FavoriteAction(user=self.voters[0], node=question, ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'FavoriteQuestion'), 1)
        self.assertEqual(self.awards(self.alice, 'StellarQuestion'), 0)
        FavoriteAction(user=self.voters[1], node=Question.objects.get(id=question.id), ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'StellarQuestion'), 1)
        first.cancel()
        FavoriteAction(user=self.voters[0], node=Question.objects.get(id=question.id), ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'StellarQuestion'), 1)


class DeletionBadgesTest(BadgeTestCase):
    def test_disciplined(self):
        self.set_setting(badge_settings.DISCIPLINED_MIN_SCORE, 1)
        question = self.ask()
        self.vote_up(question, self.voters[:1])
        DeleteAction(user=self.alice, node=Question.objects.get(id=question.id), ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'Disciplined'), 1)
        self.assertEqual(self.awards(self.alice, 'PeerPressure'), 0)

    def test_peer_pressure(self):
        self.set_setting(badge_settings.PEER_PRESSURE_MAX_SCORE, -1)
        question = self.ask()
        VoteDownAction(user=self.voters[0], node=question, ip=IP).save()
        DeleteAction(user=self.alice, node=Question.objects.get(id=question.id), ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'PeerPressure'), 1)
        self.assertEqual(self.awards(self.alice, 'Disciplined'), 0)

    def test_not_for_deleting_someone_elses_post(self):
        self.set_setting(badge_settings.PEER_PRESSURE_MAX_SCORE, 0)
        moderator = self.create_user('moderator', is_staff=True)
        DeleteAction(user=moderator, node=self.ask(), ip=IP).save()
        self.assertEqual(self.awards(moderator, 'PeerPressure'), 0)
        self.assertEqual(self.awards(self.alice, 'PeerPressure'), 0)


class VotingBadgesTest(BadgeTestCase):
    def test_supporter_and_critic_only_once(self):
        voter = self.voters[0]
        questions = [self.ask(title='Question number %d about CSV' % n) for n in range(4)]
        self.vote_up(questions[0], [voter])
        self.vote_up(questions[1], [voter])
        VoteDownAction(user=voter, node=questions[2], ip=IP).save()
        VoteDownAction(user=voter, node=questions[3], ip=IP).save()
        self.assertEqual(self.awards(voter, 'Supporter'), 1)
        self.assertEqual(self.awards(voter, 'Critic'), 1)

    def test_civic_duty(self):
        self.set_setting(badge_settings.CIVIC_DUTY_VOTES, 3)
        voter = self.voters[0]
        questions = [self.ask(title='Question number %d about CSV' % n) for n in range(4)]
        self.vote_up(questions[0], [voter])
        VoteDownAction(user=voter, node=questions[1], ip=IP).save()
        self.assertEqual(self.awards(voter, 'CivicDuty'), 0)
        self.vote_up(questions[2], [voter])
        self.assertEqual(self.awards(voter, 'CivicDuty'), 1)
        self.vote_up(questions[3], [voter])
        self.assertEqual(self.awards(voter, 'CivicDuty'), 1)


class EditingBadgesTest(BadgeTestCase):
    def test_editor_and_strunk_and_white(self):
        self.set_setting(badge_settings.STRUNK_AND_WHITE_EDITS, 2)
        editor = self.create_user('editor', reputation=5000)
        question = self.ask()
        self.revise(question, editor, 'A first edit of the question body, long enough.')
        self.assertEqual(self.awards(editor, 'Editor'), 1)
        self.assertEqual(self.awards(editor, 'StrunkAndWhite'), 0)
        self.revise(Question.objects.get(id=question.id), editor, 'A second edit of the question body, long enough.')
        self.revise(Question.objects.get(id=question.id), editor, 'A third edit of the question body, long enough.')
        self.assertEqual(self.awards(editor, 'Editor'), 1)
        self.assertEqual(self.awards(editor, 'StrunkAndWhite'), 1)

    def test_organizer(self):
        editor = self.create_user('editor', reputation=5000)
        question = self.ask()
        RetagAction(user=editor, node=question, ip=IP).save(data=dict(tagnames='csv export'))
        RetagAction(user=editor, node=Question.objects.get(id=question.id), ip=IP).save(data=dict(tagnames='csv'))
        self.assertEqual(self.awards(editor, 'Organizer'), 1)

    def test_cleanup(self):
        editor = self.create_user('editor', reputation=5000)
        question = self.ask()
        self.revise(question, self.alice, 'A changed question body that is long enough.')
        question = Question.objects.get(id=question.id)
        first = question.revisions.get(revision=1)
        RollbackAction(user=editor, node=question, ip=IP).save(data=dict(activate=first))
        question = Question.objects.get(id=question.id)
        RollbackAction(user=editor, node=question, ip=IP).save(data=dict(activate=question.revisions.get(revision=2)))
        self.assertEqual(self.awards(editor, 'Cleanup'), 1)
        self.assertEqual(self.awards(self.alice, 'Cleanup'), 0)


class TaxonomistTest(BadgeTestCase):
    def test_awarded_to_the_tag_creator_when_enough_questions_use_it(self):
        self.set_setting(badge_settings.TAXONOMIST_USE_COUNT, 3)
        bob = self.create_user('bob')
        self.ask(title='First question about shapefiles', tags='shapefile')
        self.ask(user=bob, title='Second question about shapefiles', tags='shapefile csv')
        self.assertEqual(self.awards(self.alice, 'Taxonomist'), 0)
        # The third use comes from a retag.
        question = self.ask(user=bob, title='Third question about shapefiles', tags='csv')
        RetagAction(user=bob, node=question, ip=IP).save(data=dict(tagnames='csv shapefile'))
        self.assertEqual(self.awards(self.alice, 'Taxonomist'), 1)
        self.assertEqual(self.awards(bob, 'Taxonomist'), 0)  # bob created no tag used 3 times
        # Editing a question that already had the tag does not award it again.
        self.revise(Question.objects.get(id=question.id), bob, 'An edited body that is long enough to post.')
        self.ask(title='Fourth question about shapefiles', tags='shapefile')
        self.assertEqual(self.awards(self.alice, 'Taxonomist'), 1)

    def test_tag_reaching_the_count_through_an_edit(self):
        self.set_setting(badge_settings.TAXONOMIST_USE_COUNT, 2)
        self.ask(title='First question about exports', tags='export')
        bob = self.create_user('bob')
        question = self.ask(user=bob, title='Second question about exports', tags='other')
        ReviseAction(user=bob, node=question, ip=IP).save(data=dict(
            title=question.title, tags='other export', text='An edited body that is long enough.', summary='tags'))
        self.assertEqual(self.awards(self.alice, 'Taxonomist'), 1)


class ProfileAndEmailBadgesTest(BadgeTestCase):
    def test_autobiographer(self):
        EditProfileAction(user=self.alice, ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'Autobiographer'), 0)
        self.alice.real_name = 'Alice Smith'
        self.alice.website = 'https://example.com/alice'
        self.alice.location = 'Rome'
        self.alice.date_of_birth = datetime.date(1990, 1, 2)
        self.alice.about = 'I map forests.'
        self.alice.save()
        EditProfileAction(user=self.alice, ip=IP).save()
        EditProfileAction(user=self.alice, ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'Autobiographer'), 1)

    def test_validated_email(self):
        EmailValidationAction(user=self.alice, ip=IP).save()
        EmailValidationAction(user=self.alice, ip=IP).save()
        self.assertEqual(self.awards(self.alice, 'ValidatedEmail'), 1)


class PunditTest(BadgeTestCase):
    def test_pundit(self):
        self.set_setting(badge_settings.PUNDIT_COMMENT_COUNT, 2)
        question = self.ask()
        commenter = self.voters[0]
        for n in range(3):
            CommentAction(user=commenter, ip=IP).save(data=dict(
                text='Comment number %d, long enough to post.' % n, parent=question))
            self.assertEqual(self.awards(commenter, 'Pundit'), 1 if n else 0)


class BadgePageTest(BadgeTestCase):
    def test_badge_page_lists_the_people_awarded(self):
        self.set_setting(badge_settings.NICE_QUESTION_VOTES_UP, 1)
        self.vote_up(self.ask(), self.voters[:1])
        badge = Badge.objects.get(cls='NiceQuestion')
        response = self.client.get(badge.get_absolute_url())
        self.assertCleanPage(response)
        self.assertContains(response, 'Question voted up 1 times')
        self.assertContains(response, self.alice.get_profile_url())
        self.assertEqual(Action.objects.filter(action_type='award', user=self.alice).count(),
                         Award.objects.filter(user=self.alice).count())
