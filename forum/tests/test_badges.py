from forum.badges.base import BadgesMeta
from forum.models import Award, Badge, Question

from .base import OSQATestCase


def badge_names(user):
    return set(Award.objects.filter(user=user).values_list('badge__cls', flat=True))


class BadgeSettingsTest(OSQATestCase):
    def test_admin_badge_settings_and_bootstrap(self):
        from forum_modules.default_badges import settings as badge_settings

        self.login(self.create_user('admin', is_superuser=True, is_staff=True))
        self.assertContains(self.client.get('/admin/settings/badges/'), 'POPULAR_QUESTION_VIEWS')

        self.assertEqual(self.client.post('/admin/go_bootstrap/').status_code, 302)
        self.assertEqual(int(badge_settings.POPULAR_QUESTION_VIEWS.value), 100)
        self.client.post('/admin/go_defaults/')
        self.assertEqual(int(badge_settings.POPULAR_QUESTION_VIEWS.value), 1000)


class BadgesTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.asker = self.create_user('alice')
        self.voter = self.create_user('bob', reputation=1000)
        self.question = Question(author=self.asker, title='A question title', body='Body text', tagnames='one')
        self.question.save()

    def test_default_badges_are_registered(self):
        for name in ('Supporter', 'Critic', 'Student', 'NiceQuestion', 'PopularQuestion', 'Necromancer'):
            self.assertIn(name, BadgesMeta.by_class)
        # Descriptions are computed from the current settings.
        self.assertIn('10', BadgesMeta.by_class['Pundit'].description)

    def test_first_up_vote_awards_supporter_and_student(self):
        self.login(self.voter)
        self.assertTrue(self.command('/vote/%d/up/' % self.question.id)['success'])

        self.assertIn('Supporter', badge_names(self.voter))
        self.assertIn('Student', badge_names(self.asker))

    def test_first_down_vote_awards_critic(self):
        self.login(self.voter)
        self.assertTrue(self.command('/vote/%d/down/' % self.question.id)['success'])
        self.assertIn('Critic', badge_names(self.voter))

    def test_badge_pages(self):
        response = self.client.get('/badges/')
        self.assertCleanPage(response)
        self.assertContains(response, 'Supporter')

        badge = Badge.objects.get(cls='Supporter')
        self.assertEqual(badge.name, 'Supporter')
        self.assertCleanPage(self.client.get(badge.get_absolute_url()))

    def test_award_message_only_shown_to_recipient(self):
        self.login(self.voter)
        self.command('/vote/%d/up/' % self.question.id)
        page = self.client.get('/').content.decode()
        self.assertIn("received a badge 'Supporter'", page)      # bob's own badge
        self.assertNotIn("received a badge 'Student'", page)     # alice's badge, triggered by bob

    def test_award_outside_a_request(self):
        from forum.actions import VoteUpAction
        VoteUpAction(user=self.voter, node=self.question, ip='127.0.0.1').save()
        self.assertIn('Supporter', badge_names(self.voter))

    def test_flag_accept_and_answer_badges(self):
        from forum.models import Answer

        self.login(self.voter)
        self.command('/flag/%d/' % self.question.id, {'prompt': 'spam'})
        self.client.post('/questions/%d/answer/' % self.question.id,
                         {'text': 'This is an answer with enough characters to pass.'})
        answer = Answer.objects.get(parent=self.question)

        # The flag cost alice reputation, so someone else votes.
        self.login(self.create_user('carol', reputation=1000))
        self.assertTrue(self.command('/vote/%d/up/' % answer.id)['success'])

        self.login(self.asker)
        self.assertTrue(self.command('/accept_answer/%d/' % answer.id)['success'])

        self.assertTrue({'CitizenPatrol', 'Teacher'} <= badge_names(self.voter))
        self.assertIn('Scholar', badge_names(self.asker))
