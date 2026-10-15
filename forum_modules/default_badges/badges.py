from datetime import timedelta
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from forum.badges.base import AbstractBadge
from forum.models import Badge, Tag
from forum.actions import *

from . import settings

class QuestionViewBadge(AbstractBadge):
    abstract = True
    listen_to = (QuestionViewAction,)

    @property
    def description(self):
        return _('Asked a question with %s views') % str(self.nviews)

    def award_to(self, action):
        if action.node.extra_count == int(self.nviews):
            return action.node.author


class PopularQuestion(QuestionViewBadge):
    name = _lazy('Popular Question')
    nviews = settings.POPULAR_QUESTION_VIEWS


class NotableQuestion(QuestionViewBadge):
    type = Badge.SILVER
    name = _lazy('Notable Question')
    nviews = settings.NOTABLE_QUESTION_VIEWS

class FamousQuestion(QuestionViewBadge):
    type = Badge.GOLD
    name = _lazy('Famous Question')
    nviews = settings.FAMOUS_QUESTION_VIEWS


class NodeScoreBadge(AbstractBadge):
    abstract = True
    listen_to = (VoteAction,)

    def award_to(self, action):
        if (action.node.node_type == self.node_type) and (action.node.score == int(self.expected_score)):
            return action.node.author


class QuestionScoreBadge(NodeScoreBadge):
    abstract = True
    node_type = "question"

    @property
    def description(self):
        return _('Question voted up %s times') % str(self.expected_score)

class NiceQuestion(QuestionScoreBadge):
    expected_score = settings.NICE_QUESTION_VOTES_UP
    name = _lazy("Nice Question")

class GoodQuestion(QuestionScoreBadge):
    type = Badge.SILVER
    expected_score = settings.GOOD_QUESTION_VOTES_UP
    name = _lazy("Good Question")

class GreatQuestion(QuestionScoreBadge):
    type = Badge.GOLD
    expected_score = settings.GREAT_QUESTION_VOTES_UP
    name = _lazy("Great Question")


class AnswerScoreBadge(NodeScoreBadge):
    abstract = True
    node_type = "answer"

    @property
    def description(self):
        return _('Answer voted up %s times') % str(self.expected_score)

class NiceAnswer(AnswerScoreBadge):
    expected_score = settings.NICE_ANSWER_VOTES_UP
    name = _lazy("Nice Answer")

class GoodAnswer(AnswerScoreBadge):
    type = Badge.SILVER
    expected_score = settings.GOOD_ANSWER_VOTES_UP
    name = _lazy("Good Answer")

class GreatAnswer(AnswerScoreBadge):
    type = Badge.GOLD
    expected_score = settings.GREAT_ANSWER_VOTES_UP
    name = _lazy("Great Answer")


class FavoriteQuestionBadge(AbstractBadge):
    abstract = True
    listen_to = (FavoriteAction,)

    @property
    def description(self):
        return _('Question favorited by %s users') % str(self.expected_count)

    def award_to(self, action):
        if (action.node.node_type == "question") and (action.node.favorite_count == int(self.expected_count)):
            return action.node.author

class FavoriteQuestion(FavoriteQuestionBadge):
    type = Badge.SILVER
    name = _lazy("Favorite Question")
    expected_count = settings.FAVORITE_QUESTION_FAVS

class StellarQuestion(FavoriteQuestionBadge):
    type = Badge.GOLD
    name = _lazy("Stellar Question")
    expected_count = settings.STELLAR_QUESTION_FAVS


class Disciplined(AbstractBadge):
    listen_to = (DeleteAction,)
    name = _lazy("Disciplined")
    @property
    def description(self):
        return _('Deleted own post with score of %s or higher') % settings.DISCIPLINED_MIN_SCORE

    def award_to(self, action):
        if (action.node.author == action.user) and (action.node.score >= int(settings.DISCIPLINED_MIN_SCORE)):
            return action.user

class PeerPressure(AbstractBadge):
    listen_to = (DeleteAction,)
    name = _lazy("Peer Pressure")
    @property
    def description(self):
        return _('Deleted own post with score of %s or lower') % settings.PEER_PRESSURE_MAX_SCORE

    def award_to(self, action):
        if (action.node.author == action.user) and (action.node.score <= int(settings.PEER_PRESSURE_MAX_SCORE)):
            return action.user


class Critic(AbstractBadge):
    award_once = True
    listen_to = (VoteDownAction,)
    name = _lazy("Critic")
    description = _lazy('First down vote')

    def award_to(self, action):
        if (action.user.vote_down_count == 1):
            return action.user


class Supporter(AbstractBadge):
    award_once = True
    listen_to = (VoteUpAction,)
    name = _lazy("Supporter")
    description = _lazy('First up vote')

    def award_to(self, action):
        if (action.user.vote_up_count == 1):
            return action.user


class FirstActionBadge(AbstractBadge):
    award_once = True
    abstract = True

    def award_to(self, action):
        if (self.listen_to[0].objects.filter(user=action.user).count() == 1):
            return action.user

class CitizenPatrol(FirstActionBadge):
    listen_to = (FlagAction,)
    name = _lazy("Citizen Patrol")
    description = _lazy('First flagged post')

class Organizer(FirstActionBadge):
    listen_to = (RetagAction,)
    name = _lazy("Organizer")
    description = _lazy('First retag')

class Editor(FirstActionBadge):
    listen_to = (ReviseAction,)
    name = _lazy("Editor")
    description = _lazy('First edit')

class Scholar(FirstActionBadge):
    listen_to = (AcceptAnswerAction,)
    name = _lazy("Scholar")
    description = _lazy('First accepted answer on your own question')

class Cleanup(FirstActionBadge):
    listen_to = (RollbackAction,)
    name = _lazy("Cleanup")
    description = _lazy('First rollback')


class Autobiographer(AbstractBadge):
    award_once = True
    listen_to = (EditProfileAction,)
    name = _lazy("Autobiographer")
    description = _lazy('Completed all user profile fields')

    def award_to(self, action):
        user = action.user
        if user.email and user.real_name and user.website and user.location and \
                user.date_of_birth and user.about:
            return user


class CivicDuty(AbstractBadge):
    type = Badge.SILVER
    award_once = True
    listen_to = (VoteUpAction, VoteDownAction)
    name = _lazy("Civic Duty")
    @property
    def description(self):
        return _('Voted %s times') % settings.CIVIC_DUTY_VOTES

    def award_to(self, action):
        if (action.user.vote_up_count + action.user.vote_down_count) == int(settings.CIVIC_DUTY_VOTES):
            return action.user


class Pundit(AbstractBadge):
    award_once = True
    listen_to = (CommentAction,)
    name = _lazy("Pundit")
    @property
    def description(self):
        return _('Left %s comments') % settings.PUNDIT_COMMENT_COUNT

    def award_to(self, action):
        if action.user.nodes.filter_state(deleted=False).filter(node_type="comment").count() == int(
                settings.PUNDIT_COMMENT_COUNT):
            return action.user


class SelfLearner(AbstractBadge):
    listen_to = (VoteUpAction, )
    name = _lazy("Self Learner")
    @property
    def description(self):
        return _('Answered your own question with at least %s up votes') % settings.SELF_LEARNER_UP_VOTES

    def award_to(self, action):
        if (action.node.node_type == "answer") and (action.node.author == action.node.parent.author) and (
        action.node.score == int(settings.SELF_LEARNER_UP_VOTES)):
            return action.node.author


class StrunkAndWhite(AbstractBadge):
    type = Badge.SILVER
    award_once = True
    listen_to = (ReviseAction,)
    name = _lazy("Strunk & White")
    @property
    def description(self):
        return _('Edited %s entries') % settings.STRUNK_AND_WHITE_EDITS

    def award_to(self, action):
        if (ReviseAction.objects.filter(user=action.user).count() == int(settings.STRUNK_AND_WHITE_EDITS)):
            return action.user


class Student(AbstractBadge):
    award_once = True
    listen_to = (VoteUpAction,)
    name = _lazy("Student")
    description = _lazy('Asked first question with at least one up vote')

    def award_to(self, action):
        if (action.node.node_type == "question") and (action.node.author.nodes.filter_state(deleted=False).filter(
                node_type="question", score=1).count() == 1):
            return action.node.author


class Teacher(AbstractBadge):
    award_once = True
    listen_to = (VoteUpAction,)
    name = _lazy("Teacher")
    description = _lazy('Answered first question with at least one up vote')

    def award_to(self, action):
        if (action.node.node_type == "answer") and (action.node.author.nodes.filter_state(deleted=False).filter(
                node_type="answer", score=1).count() == 1):
            return action.node.author


class Enlightened(AbstractBadge):
    type = Badge.SILVER
    award_once = True
    listen_to = (VoteUpAction, AcceptAnswerAction)
    name = _lazy("Enlightened")
    @property
    def description(self):
        return _('First answer was accepted with at least %s up votes') % settings.ENLIGHTENED_UP_VOTES

    def award_to(self, action):
        if (action.node.node_type == "answer") and (action.node.accepted) and (
        action.node.score >= int(settings.ENLIGHTENED_UP_VOTES)):
            return action.node.author


class Guru(AbstractBadge):
    type = Badge.SILVER
    listen_to = (VoteUpAction, AcceptAnswerAction)
    name = _lazy("Guru")
    @property
    def description(self):
        return _('Accepted answer and voted up %s times') % settings.GURU_UP_VOTES

    def award_to(self, action):
        if (action.node.node_type == "answer") and (action.node.accepted) and (
        action.node.score >= int(settings.GURU_UP_VOTES)):
            return action.node.author


class Necromancer(AbstractBadge):
    type = Badge.SILVER
    listen_to = (VoteUpAction,)
    name = _lazy("Necromancer")
    @property
    def description(self):
        return _('Answered a question more than %(dif_days)s days later with at least %(up_votes)s votes') % \
            {'dif_days': settings.NECROMANCER_DIF_DAYS, 'up_votes': settings.NECROMANCER_UP_VOTES}

    def award_to(self, action):
        if (action.node.node_type == "answer") and (
        action.node.added_at >= (action.node.question.added_at + timedelta(days=int(settings.NECROMANCER_DIF_DAYS)))
        ) and (int(action.node.score) == int(settings.NECROMANCER_UP_VOTES)):
            return action.node.author

class Taxonomist(AbstractBadge):
    type = Badge.SILVER
    # Questions get their tags when asked, edited or retagged.
    listen_to = (AskAction, ReviseAction, RetagAction)
    name = _lazy("Taxonomist")
    @property
    def description(self):
        return _('Created a tag used by %s questions') % settings.TAXONOMIST_USE_COUNT

    def award_to(self, action):
        question = action.node
        if question is None or question.node_type != "question" or question.nis.deleted:
            return None

        # Only the tags this action gave the question: an edit that keeps a tag
        # must not count it again.
        added = set(question.tagname_list())
        if not isinstance(action, AskAction):
            try:
                previous = question.revisions.get(revision=int(action.extra) - 1)
            except Exception:
                return None
            added -= set(previous.tagname_list())

        for tag in Tag.objects.filter(name__in=added).select_related('created_by'):
            if tag.used_count == int(settings.TAXONOMIST_USE_COUNT):
                return tag.created_by

class ValidatedEmail(AbstractBadge):
    type = Badge.BRONZE
    listen_to = (EmailValidationAction,)
    name = _lazy("Validated Email")
    description = _lazy("User who has validated email associated to the account")
    award_once = True

    def award_to(self, action):
        return action.user
