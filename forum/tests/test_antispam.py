from datetime import datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from forum import settings
from forum.actions import UserLoginAction
from forum.models import User, Question, ModerationReport

from .base import OSQATestCase
from .test_ai import AITestCase


class SettingMixin:
    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)


class SignupBotTrapTest(SettingMixin, OSQATestCase):
    def register(self, username='carol', ip='10.0.0.1', **fields):
        data = {'username': username, 'email': '%s@example.com' % username, 'password1': 's3cret-pass',
                'password2': 's3cret-pass', 'next': '/', 'form_token': self.signup_token()}
        data.update(fields)
        return self.client.post('/account/local/register/', data, REMOTE_ADDR=ip)

    def test_form_carries_the_traps(self):
        page = self.client.get('/account/local/register/').content.decode()
        self.assertIn('name="homepage"', page)
        self.assertIn('name="form_token"', page)
        self.assertIn('aria-hidden="true"', page)

    def test_person_can_register(self):
        self.assertEqual(self.register().status_code, 302)
        self.assertTrue(User.objects.filter(username='carol').exists())

    def test_bots_are_refused(self):
        self.assertContains(self.register(homepage='http://spam.example'), 'could not be completed')
        self.assertContains(self.register(form_token=self.signup_token(age=1)), 'That was quick')
        self.assertContains(self.register(form_token='forged'), 'expired')
        self.assertContains(self.register(form_token=self.signup_token(age=2 * 24 * 3600)), 'expired')
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_signups_per_ip_are_limited(self):
        self.set(settings.SIGNUP_LIMIT_PER_IP, 2)
        self.assertEqual(self.register('carol').status_code, 302)
        self.client.logout()
        self.assertEqual(self.register('dave').status_code, 302)
        self.client.logout()
        self.assertContains(self.register('erin'), 'Too many accounts')
        self.assertEqual(self.register('erin', ip='10.0.0.2').status_code, 302)

    def test_external_signup_form_has_the_traps(self):
        from forum.forms import PublicRegistrationForm
        data = {'next': '/', 'username': 'carol', 'email': 'carol@example.com', 'real_name': 'Carol',
                'username_validation': '', 'username_validation_sum': '14', 'form_token': self.signup_token()}
        self.assertTrue(PublicRegistrationForm(data).is_valid())
        self.assertFalse(PublicRegistrationForm(dict(data, homepage='x')).is_valid())


class ProfileLinksTest(SettingMixin, OSQATestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user('spammy', website='http://cheap-pills.example',
                                     about='Buy [cheap pills](http://cheap-pills.example) **today**')

    def profile(self):
        return self.client.get(self.user.get_profile_url()).content.decode()

    def test_low_reputation_profiles_have_no_links(self):
        page = self.profile()
        self.assertIn('<meta name="robots" content="noindex, nofollow" />', page)
        self.assertNotIn('href="http://cheap-pills.example"', page)
        self.assertIn('http://cheap-pills.example', page)
        self.assertIn('Buy cheap pills <strong>today</strong>', page)

    def test_trusted_profiles_keep_nofollow_links(self):
        self.user.reputation = 25
        self.user.save()
        page = self.profile()
        self.assertNotIn('name="robots"', page)
        self.assertIn('<a rel="nofollow ugc noopener" target="_blank" href="http://cheap-pills.example">', page)
        self.assertIn('<a href="http://cheap-pills.example" rel="nofollow ugc noopener noreferrer">cheap pills</a>', page)
        self.assertNotIn('&lt;p&gt;', page)


class ProfileReviewTest(AITestCase):
    SPAM = {'verdict': 'spam', 'confidence': 0.9, 'reason': 'SEO links to a pharmacy.'}

    def setUp(self):
        super().setUp()
        self.bot = self.create_user('pillbot')
        self.mod = self.create_user('mod', is_staff=True)

    def edit_profile(self, user, **fields):
        self.login(user)
        data = {'email': user.email, 'realname': '', 'website': 'http://cheap-pills.example', 'city': '',
                'birthday': '', 'about': 'Best online pharmacy, no prescription.'}
        data.update(fields)
        response = self.client.post(reverse('edit_user', args=[user.id, user.username]), data)
        self.assertEqual(response.status_code, 302)

    def test_spam_profiles_are_reported(self):
        fake = self.gemini(self.SPAM)
        self.edit_profile(self.bot)
        report = ModerationReport.objects.get(user=self.bot)
        self.assertEqual((report.node, report.verdict, report.reason), (None, 'spam', 'SEO links to a pharmacy.'))
        self.assertIn('<website>http://cheap-pills.example</website>', fake.prompt)
        self.assertIn('<email_domain>example.com</email_domain>', fake.prompt)
        self.assertNotIn('pillbot@example.com', fake.prompt)
        self.assertTrue(User.objects.get(id=self.bot.id).is_active)

    def test_empty_or_trusted_profiles_are_not_reviewed(self):
        fake = self.gemini(self.SPAM)
        self.edit_profile(self.bot, website='', about='')
        self.edit_profile(self.mod)
        self.set(self.ai.AI_PROFILE_REVIEW, False)
        self.edit_profile(self.bot)
        self.assertEqual(fake.requests, [])

    def resolve(self, resolution):
        report = ModerationReport.objects.get(user=self.bot, resolved_at=None)
        self.login(self.mod)
        return self.client.post(reverse('admin_resolve_moderation_report', args=[report.id]), {'resolution': resolution})

    def test_moderators_suspend_or_delete_accounts(self):
        self.gemini(self.SPAM)
        self.edit_profile(self.bot)
        self.login(self.mod)
        page = self.client.get(reverse('admin_flagged_posts'))
        self.assertContains(page, 'Delete account')
        self.assertContains(page, 'SEO links to a pharmacy.')

        self.resolve('suspend')
        self.assertFalse(User.objects.get(id=self.bot.id).is_active)
        self.assertEqual(ModerationReport.objects.get(user=self.bot).resolution, 'suspended')

        ModerationReport.objects.create(user=self.bot, source='ai', verdict='spam', confidence=0.9)
        self.resolve('delete_account')
        self.assertFalse(User.objects.filter(id=self.bot.id).exists())

    def test_accounts_with_posts_are_not_deleted(self):
        self.gemini(self.SPAM)
        self.edit_profile(self.bot)
        Question(author=self.bot, title='A real question title', tagnames='x', body='A body that is long enough.').save()
        self.resolve('delete_account')
        self.assertTrue(User.objects.filter(id=self.bot.id).exists())


class LastSeenTest(OSQATestCase):
    def test_last_seen_is_updated_hourly(self):
        user = self.create_user('reader')
        User.objects.filter(id=user.id).update(last_seen=datetime(2020, 1, 1))
        self.login(user)
        seen = lambda: User.objects.filter(id=user.id).values_list('last_seen', flat=True)[0]
        self.client.get('/')
        self.assertGreater(seen(), datetime.now() - timedelta(minutes=1))
        User.objects.filter(id=user.id).update(last_seen=datetime(2020, 1, 1))
        self.client.get('/')
        self.assertEqual(seen(), datetime(2020, 1, 1))


class DormantAccountCleanupTest(OSQATestCase):
    def user(self, name, joined_days_ago=30, **fields):
        user = self.create_user(name, **fields)
        joined = datetime.now() - timedelta(days=joined_days_ago)
        User.objects.filter(id=user.id).update(date_joined=joined, last_seen=joined)
        return User.objects.get(id=user.id)

    def setUp(self):
        super().setUp()
        self.bot = self.user('bot')
        UserLoginAction(user=self.bot, ip='1.2.3.4').save()
        self.other_bot = self.user('bot2', website='http://spam.example')
        self.confirmed = self.user('confirmed', email_isvalid=True)
        self.newcomer = self.user('newcomer', joined_days_ago=3)
        self.asker = self.user('asker')
        Question(author=self.asker, title='A real question title', tagnames='x', body='A body that is long enough.').save()
        self.reader = self.user('reader')
        User.objects.filter(id=self.reader.id).update(last_seen=datetime.now() - timedelta(days=1))
        self.returning = self.user('returning')
        User.objects.filter(id=self.returning.id).update(last_login=datetime.now() - timedelta(days=2))
        self.staff = self.user('staffer', is_staff=True)

    def run_command(self, *args):
        out = StringIO()
        call_command('cleanup_inactive_users', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_without_deleting(self):
        output = self.run_command('--dry-run')
        self.assertIn('Would delete bot ', output)
        self.assertIn('2 dormant account(s) older than 15 days would be deleted', output)
        self.assertTrue(User.objects.filter(username='bot').exists())

    def test_only_dormant_accounts_are_deleted(self):
        self.assertIn('Deleted 2 dormant account(s)', self.run_command())
        self.assertEqual(set(User.objects.values_list('username', flat=True)),
                         {'confirmed', 'newcomer', 'asker', 'reader', 'returning', 'staffer'})

    def test_threshold_setting(self):
        settings.INACTIVE_ACCOUNT_DAYS.set_value(0)
        self.addCleanup(setattr, settings.INACTIVE_ACCOUNT_DAYS, '_temp', None)
        self.assertIn('turned off', self.run_command())
        self.assertEqual(User.objects.count(), 8)
        self.assertIn('Deleted 0', self.run_command('--days', '60'))
