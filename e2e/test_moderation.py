"""Moderation and accounts in the browser: suspending and reinstating a
user, closing and reopening, deleting and undeleting, bulk moderation in
Admin > Tools, moderator rights, converting posts into questions, @mention
notifications, the contact form for members, badge pages and avatars."""
import datetime
from hashlib import md5

from django.urls import reverse

from forum import settings
from forum.models import Action, Answer, Badge, Comment, Question, User
from forum_modules.default_badges import settings as badge_settings

from .base import E2ETestCase, PASSWORD


class SuspensionTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', reputation=1000, is_superuser=True, is_staff=True)
        self.spammer = self.create_user('spammer', reputation=10)

    def sign_in(self, page, username):
        self.visit(page, '/account/signin/')
        page.fill('#id_username', username)
        page.fill('#id_password', PASSWORD)
        with page.expect_navigation():
            page.click('#blogin')
        self.check_page(page)

    def suspension_menu(self, page):
        self.visit(page, self.spammer.get_profile_url())
        page.click('#user-menu')
        page.click('#user-menu-dropdown a[href="%s"]' % reverse('user_suspend', kwargs={'id': self.spammer.id}))

    def test_suspend_for_a_day_then_withdraw(self):
        admin = self.open(self.admin)
        self.suspension_menu(admin)
        admin.wait_for_selector('.dialog select[name=bantype]')
        admin.select_option('.dialog select[name=bantype]', 'forxdays')
        admin.fill('.dialog input[name=forxdays]', '1')
        self.confirm_dialog(admin, {'textarea[name=publicmsg]': 'Advertising <b>x</b>',
                                    'textarea[name=privatemsg]': 'Stop posting links <b>x</b>'})
        spammer = User.objects.get(id=self.spammer.id)
        self.assertTrue(spammer.is_suspended())
        self.assertEqual(spammer.suspension.extra['forxdays'], 1)

        # The public message is in the activity log, as text.
        self.visit(admin, reverse('user_recent', kwargs={'id': self.spammer.id, 'slug': 'spammer'}))
        self.assertIn('Advertising <b>x</b>', admin.locator('body').inner_text())
        self.assertEqual(admin.locator('body b:text-is("x")').count(), 0)

        # The suspended user can't sign in, and is told why (as text, not HTML).
        page = self.open()
        self.sign_in(page, 'spammer')
        notice = page.locator('.notify')
        notice.wait_for()
        self.assertIn('this account is suspended', notice.inner_text())
        self.assertIn('Stop posting links <b>x</b>', notice.inner_text())
        self.assertEqual(notice.locator('b').count(), 0)
        self.assertNotIn('spammer', page.locator('#top').inner_text())

        # The administrator withdraws the suspension.
        self.suspension_menu(admin)
        self.assertIn('withdraw suspension', admin.locator('#user-menu-dropdown').inner_text())
        self.confirm_dialog(admin)
        self.assertFalse(User.objects.get(id=self.spammer.id).is_suspended())

        self.sign_in(page, 'spammer')
        self.assertIn('spammer', page.locator('#top').inner_text())
        self.visit(page, '/questions/ask/')
        page.fill('#id_title', 'How do I export Collect survey data to CSV?')
        page.fill('#editor', 'I would like to export the data of a survey as CSV files.')
        page.fill('#id_tags', 'collect')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        self.assertTrue(Question.objects.filter(author=self.spammer).exists())

    def test_suspension_past_its_end_lifts_and_indefinite_stays(self):
        from forum.actions import SuspendAction
        other = self.create_user('other', reputation=10)
        SuspendAction(user=self.admin, ip='127.0.0.1').save(data={
            'bantype': 'forxdays', 'forxdays': 1, 'publicmsg': 'x', 'privatemsg': 'one day', 'suspended': self.spammer})
        SuspendAction(user=self.admin, ip='127.0.0.1').save(data={
            'bantype': 'indefinitely', 'publicmsg': 'x', 'privatemsg': 'for good', 'suspended': other})
        Action.objects.filter(action_type='suspend').update(
            action_date=datetime.datetime.now() - datetime.timedelta(days=2))

        page = self.open()
        self.sign_in(page, 'spammer')
        self.assertIn('spammer', page.locator('#top').inner_text())
        self.assertTrue(User.objects.get(id=self.spammer.id).is_active)

        page = self.open()
        self.sign_in(page, 'other')
        self.assertIn('for good', page.locator('.notify').inner_text())
        self.assertNotIn('other', page.locator('#top').inner_text())


class CloseDeleteTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.moderator = self.create_user('moderator', reputation=10, is_staff=True)
        self.alice = self.create_user('alice', reputation=300)
        self.bob = self.create_user('bob', reputation=100)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')

    def open_close_dialog(self, page):
        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % self.question.id)
        page.click('a[href="%s"]' % reverse('close', kwargs={'id': self.question.id}))
        page.wait_for_selector('.dialog textarea[name=prompt]')

    def test_close_with_a_reason_and_an_empty_reason(self):
        page = self.open(self.moderator)
        self.open_close_dialog(page)
        self.confirm_dialog(page, {'textarea[name=prompt]': ''})
        page.wait_for_selector('.dialog.warning')
        self.assertIn('Reason is empty', page.locator('.dialog.warning').inner_text())
        self.assertFalse(Question.objects.get(id=self.question.id).nis.closed)

        self.open_close_dialog(page)
        self.confirm_dialog(page, {'textarea[name=prompt]': 'Off topic for this forum'})
        self.assertTrue(Question.objects.get(id=self.question.id).nis.closed)
        self.visit(page, self.question.get_absolute_url())
        self.assertIn('Off topic for this forum', page.locator('#main-body').inner_text())

    def test_owner_closes_and_reopens_own_question(self):
        self.set_setting(settings.REP_TO_CLOSE_OWN, 250)
        page = self.open(self.alice)
        self.open_close_dialog(page)
        self.confirm_dialog(page, {'textarea[name=prompt]': 'Found the answer in the manual'})
        self.assertTrue(Question.objects.get(id=self.question.id).nis.closed)

        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % self.question.id)
        self.click_command(page, 'a[href="%s"]' % reverse('reopen', kwargs={'id': self.question.id}))
        self.assertFalse(Question.objects.get(id=self.question.id).nis.closed)

        # Without the reputation the controls aren't there.
        page = self.open(self.bob)
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('a[href="%s"]' % reverse('close', kwargs={'id': self.question.id})).count(), 0)

    def test_owner_cannot_delete_an_answered_question_but_a_moderator_can_and_undeletes(self):
        self.create_answer(self.bob, self.question, 'Use the export menu of the data page, then pick CSV.')
        delete_link = 'a[href="%s"]' % reverse('delete_post', kwargs={'id': self.question.id})
        page = self.open(self.alice)
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('#post-%d ' % self.question.id + delete_link).count(), 0)

        page = self.open(self.moderator)
        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % self.question.id)
        page.click(delete_link)
        self.confirm_dialog(page)
        self.assertTrue(Question.objects.get(id=self.question.id).nis.deleted)

        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % self.question.id)
        undelete = page.locator(delete_link).filter(has_text='undelete')
        undelete.click()
        self.confirm_dialog(page)
        self.assertFalse(Question.objects.get(id=self.question.id).nis.deleted)


class BulkModerationTest(E2ETestCase):
    def test_bulk_close_delete_and_undelete(self):
        admin = self.create_user('admin', reputation=1000, is_superuser=True, is_staff=True)
        alice = self.create_user('alice', reputation=100)
        questions = [self.create_question(alice, title) for title in (
            'How do I export Collect survey data to CSV?', 'Can Collect Earth use Planet imagery?',
            'Where are the Arena backups stored?')]
        chosen = questions[:2]
        state = lambda name: [getattr(Question.objects.get(id=q.id).nis, name) for q in questions]

        page = self.open(admin)

        def run(action, message):
            self.visit(page, '/admin/tools/nodeman/?node_type=question')
            for question in chosen:
                page.check('input[name=_selected_node][value="%d"]' % question.id)
            page.select_option('#changelist-form select[name=action]', action)
            with page.expect_navigation():
                page.click('#changelist-form button[name=execute]')
            self.check_page(page)
            self.assertIn(message, page.locator('body').inner_text())

        self.visit(page, '/admin/tools/nodeman/?node_type=question')
        self.assertEqual(page.locator('a:text-is("%s")' % questions[0].title).count(), 1)

        run('close_selected', 'Selected questions were closed')
        self.assertEqual(state('closed'), [True, True, False])
        run('delete_selected', 'All selected nodes marked as deleted')
        self.assertEqual(state('deleted'), [True, True, False])
        run('undelete_selected', 'All selected nodes undeleted')
        self.assertEqual(state('deleted'), [False, False, False])


class ModeratorRightsTest(E2ETestCase):
    def test_grant_and_revoke(self):
        admin = self.create_user('admin', reputation=1000, is_superuser=True, is_staff=True)
        alice = self.create_user('alice', reputation=100)
        page = self.open(admin)

        def toggle(action):
            self.visit(page, alice.get_profile_url())
            page.click('#user-menu')
            page.click('#user-menu-dropdown a[href="%s"]' % reverse('user_powers', kwargs={
                'id': alice.id, 'action': action, 'status': 'staff'}))
            self.confirm_dialog(page)

        toggle('grant')
        self.assertTrue(User.objects.get(id=alice.id).is_staff)
        alice_page = self.open(alice)
        self.visit(alice_page, '/admin/support/')
        self.visit(alice_page, reverse('admin_flagged_posts'))

        toggle('remove')
        self.assertFalse(User.objects.get(id=alice.id).is_staff)
        self.visit(page, alice.get_profile_url())
        page.click('#user-menu')
        self.assertIn('grant moderator status', page.locator('#user-menu-dropdown').inner_text())
        for path in ('/admin/support/', reverse('admin_flagged_posts'), '/admin/'):
            with self.subTest(path=path):
                self.assertIn(alice_page.goto(self.url(path)).status, (401, 403))
                alice_page.problems.clear()


class ConvertToQuestionTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.moderator = self.create_user('moderator', reputation=10, is_staff=True)
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=10)
        self.question = self.create_question(self.alice, 'How do I add a new plot layout to Collect?')

    def save_as_question(self, page, title):
        self.check_page(page)
        page.fill('#id_title', title)
        page.fill('#id_tags', 'collect')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)

    def test_moderator_converts_a_comment(self):
        comment = self.create_comment(self.bob, self.question, 'And can plots be shared between two surveys?')
        page = self.open(self.moderator)
        self.visit(page, self.question.get_absolute_url())
        with page.expect_navigation():
            page.click('#comment-%d-convert-to-question' % comment.id)
        self.save_as_question(page, 'Can plots be shared between Collect surveys?')
        converted = Question.objects.get(id=comment.id)
        self.assertEqual(converted.author, self.bob)
        self.assertIsNone(converted.parent)
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())
        self.assertIn('Can plots be shared between Collect surveys?', page.locator('h1').first.inner_text())

    def test_owner_converts_own_answer(self):
        self.set_setting(settings.REP_TO_CONVERT_TO_QUESTION, 2000)
        answer = self.create_answer(self.bob, self.question, 'Actually, I have the same problem with plot layouts.')
        page = self.open(self.bob)
        self.visit(page, self.question.get_absolute_url())
        page.click('#node-%d-menu-trigger' % answer.id)
        page.locator('#node-%d-menu-dropdown a' % answer.id).filter(has_text='convert to question').click()
        page.wait_for_url('**/convert_to_question/%d/' % answer.id)
        self.save_as_question(page, 'Why does my plot layout disappear in Collect?')
        self.assertEqual(Question.objects.get(id=answer.id).author, self.bob)
        self.assertFalse(Answer.objects.filter(id=answer.id).exists())

        # Someone else's answer, without the reputation: no menu item.
        other = self.create_answer(self.alice, self.question, 'Open the survey designer and add the layout.')
        self.visit(page, self.question.get_absolute_url())
        self.assertEqual(page.locator('#node-%d-menu-dropdown a' % other.id).filter(
            has_text='convert to question').count(), 0)
        self.assertEqual(page.locator('a[href^="%s"]' % reverse('convert_to_question', kwargs={'id': other.id})).count(), 0)


class MentionsAndContactTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bob', reputation=500)
        self.carol = self.create_user('carol', reputation=500)

    def bell_count(self, page):
        self.visit(page, '/')
        return page.locator('#notification-bell .notification-count').inner_text().strip()

    def test_mentions_in_a_question_and_in_an_edit(self):
        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        page.fill('#id_title', 'How do I export Collect survey data to CSV?')
        page.fill('#editor', 'Hello @bob, how do I export the survey data as CSV?')
        page.fill('#id_tags', 'collect')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        question = Question.objects.get(author=self.alice)

        bob = self.open(self.bob)
        self.assertEqual(self.bell_count(bob), '1')
        with bob.expect_navigation():
            bob.click('#notification-bell')
        self.check_page(bob)
        self.assertIn('alice mentioned you', bob.locator('.inbox-list').inner_text())

        self.visit(page, reverse('edit_question', kwargs={'id': question.id}))
        page.fill('#editor', 'Hello @bob and @carol, how do I export the survey data as CSV?')
        page.fill('#id_summary', 'ask carol too')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)

        carol = self.open(self.carol)
        self.assertEqual(self.bell_count(carol), '1')
        self.visit(carol, '/notifications/')
        self.assertIn('alice mentioned you', carol.locator('.inbox-list').inner_text())
        self.assertIn(question.title, carol.locator('.inbox-list').inner_text())

    def test_contact_form_of_a_member(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        page = self.open(self.alice)
        self.visit(page, '/faq/')
        with page.expect_navigation():
            page.locator('a[href^="%s"]' % reverse('feedback')).first.click()
        self.check_page(page)
        self.assertEqual(page.locator('#id_name, #id_email').count(), 0)
        page.fill('#id_message', 'The CSV export link on the FAQ page is broken.')
        with page.expect_navigation():
            page.get_by_role('button', name='Send Feedback').click()
        self.check_page(page)
        self.assertIn('Thanks for the feedback', page.locator('body').inner_text())
        mails = self.mail_to(admin)
        self.assertEqual(len(mails), 1)
        self.assertIn('From: alice', mails[0][3])
        self.assertIn('Email: alice@example.com', mails[0][3])
        self.assertEqual([m for m in self.sent_mail if m[0] != admin], [])


class BadgesAndAvatarTest(E2ETestCase):
    def test_badge_pages_list_the_people_awarded(self):
        self.set_setting(badge_settings.NICE_QUESTION_VOTES_UP, 1)
        self.set_setting(badge_settings.TAXONOMIST_USE_COUNT, 2)
        alice = self.create_user('alice', reputation=100)
        bob = self.create_user('bob', reputation=100)
        question = self.create_question(alice, 'How do I reproject a shapefile in Collect Earth?', tags='shapefile')
        self.create_question(bob, 'Can I import a shapefile as a plot grid?', tags='shapefile')

        page = self.open(bob)
        self.visit(page, question.get_absolute_url())
        self.click_command(page, '#post-%d-upvote' % question.id)

        for cls, text in (('NiceQuestion', 'Question voted up 1 times'),
                          ('Taxonomist', 'Created a tag used by 2 questions')):
            with self.subTest(badge=cls):
                badge = Badge.objects.get(cls=cls)
                self.visit(page, '/badges/')
                with page.expect_navigation():
                    page.locator('a.medal[href="%s"]' % badge.get_absolute_url()).first.click()
                self.check_page(page)
                self.assertIn(text, page.locator('#main-body').inner_text())
                self.assertIn('alice', page.locator('#award-list').inner_text())
                self.assertNotIn('bob', page.locator('#award-list').inner_text())

    def test_profile_avatar(self):
        alice = self.create_user('alice', reputation=100)
        digest = md5(b'alice@example.com').hexdigest()
        page = self.open(device='mobile', dark=True)
        # Gravatar answers with a tiny image, so the page keeps its own avatar URL
        # (osqa.avatars.js swaps in the initial only when the image fails to load).
        pixel = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                              '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')
        page.route('https://secure.gravatar.com/**', lambda route: route.fulfill(
            status=200, content_type='image/png', body=pixel))
        self.visit(page, alice.get_profile_url())
        avatar = page.locator('img.gravatar[width="128"]').first
        self.assertEqual(avatar.get_attribute('src'),
                         'https://secure.gravatar.com/avatar/%s?s=128&d=identicon&r=g' % digest)
        self.assertEqual(avatar.get_attribute('alt'), 'alice')
        self.assertEqual(avatar.get_attribute('height'), '128')

        self.set_setting(settings.GRAVATAR_DEFAULT_IMAGE, 'mm')
        self.visit(page, alice.get_profile_url())
        self.assertIn('d=mm', page.locator('img.gravatar[width="128"]').first.get_attribute('src'))
