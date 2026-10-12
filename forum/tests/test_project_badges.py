from forum.actions import VoteUpAction
from forum.badges.base import BadgesMeta
from forum.models import Award, Question

from .base import OSQATestCase


class ProjectBadgesTest(OSQATestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The module is disabled in the test settings; importing it registers the badge.
        from forum_modules.project_badges import badges, settings
        cls.badge_settings = settings

    def setUp(self):
        super().setUp()
        self.asker = self.create_user('alice')
        self.voters = [self.create_user('voter%d' % n, reputation=1000) for n in range(3)]

    def bug_busters(self):
        return Award.objects.filter(user=self.asker, badge__cls='BugBuster').count()

    def vote_up(self, question, voters):
        for voter in voters:
            VoteUpAction(user=voter, node=question, ip='127.0.0.1').save()

    def test_badge_is_registered(self):
        badge = BadgesMeta.by_class['BugBuster']
        self.assertIn(str(self.badge_settings.BUG_BUSTER_VOTES_UP), badge.description)
        self.assertIn('"bug"', badge.description)

    def test_awarded_for_upvoted_bug_question(self):
        question = Question(author=self.asker, title='Crash on startup', body='It crashes', tagnames='bug crash')
        question.save()

        self.vote_up(question, self.voters[:2])
        self.assertEqual(self.bug_busters(), 0)

        self.vote_up(question, self.voters[2:])
        self.assertEqual(Question.objects.get(id=question.id).score, 3)
        self.assertEqual(self.bug_busters(), 1)

    def test_not_awarded_without_bug_tag(self):
        question = Question(author=self.asker, title='Feature request', body='Please add', tagnames='feature')
        question.save()

        self.vote_up(question, self.voters)
        self.assertEqual(self.bug_busters(), 0)
