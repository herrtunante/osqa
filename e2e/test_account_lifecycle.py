"""The account lifecycle through the real interface, with the emails it sends
(forum/utils/accounts.py): signing up with the confirmation email (double
opt-in) and posting once confirmed, confirming before signing in, the password
strength meter, forgotten passwords, changing the password (other sessions
are signed out), changing the email address (confirm the new one, cancel
from the old one), signing out with a POST (and everywhere), and deleting the
account. On a desktop and on a phone in dark mode; the new pages are also
checked with axe-core."""
import re

from django.urls import reverse

from forum import settings
from forum.models import Question, User

from playwright.sync_api import Error as PlaywrightError

from .base import E2ETestCase, PASSWORD
from .test_accessibility import AccessibilityTest

VIEWS = (('desktop', False), ('mobile', True))
NEW_PASSWORD = 'a-new-long-password-9'


class AccountLifecycleTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user('owner', is_superuser=True, is_staff=True)  # the site owner

    # -- helpers -------------------------------------------------------------

    def mails(self, subject, email=None):
        return [m for m in self.sent_mail if subject in m[1] and (email is None or m[0].email == email)]

    def link(self, mail, kind):
        return re.search(r'http://testserver(/account/%s/\d+/[^/\s]+/)' % kind, mail[3]).group(1)

    def submit(self, page, selector):
        try:
            with page.expect_navigation():
                page.click(selector)
        except PlaywrightError:
            # A POST that redirects can show up as an aborted navigation first.
            page.wait_for_load_state('load')
        self.check_page(page)

    def visit_gone(self, page, path):
        """A used or expired link: the page explains it, with status 410
        (which the browser also logs in the console)."""
        response = page.goto(self.url(path))
        self.assertEqual(response.status, 410, path)
        page.wait_for_load_state('load')
        page.problems[:] = [p for p in page.problems if 'status of 410' not in p]
        self.check_page(page, path)
        self.assertIn('doesn', page.locator('.auth-card h1').inner_text())

    def signed_in_as(self, page):
        top = page.locator('#top').inner_text()
        return next((u.username for u in User.objects.all() if u.username in top), None)

    def sign_in(self, page, username, password):
        self.visit(page, '/account/signin/')
        page.fill('#id_username', username)
        page.fill('#id_password', password)
        self.submit(page, '#blogin')

    def sign_up(self, page, username, password='green-tree-river-7'):
        self.visit(page, '/account/local/register/')
        page.fill('#id_username', username)
        page.fill('#id_email', '%s@example.com' % username)
        page.fill('#id_password1', 'short')
        meter = page.locator('.password-meter[data-meter-for="id_password1"]')
        meter.wait_for()
        self.assertEqual(meter.get_attribute('data-level'), '0')
        self.assertIn('Too short', meter.inner_text())
        self.assertEqual(page.locator('[data-rule="length"].is-met').count(), 0)
        page.fill('#id_password1', username + '123')
        self.assertIn('Too close to your username', meter.inner_text())
        page.fill('#id_password1', password)
        self.assertEqual(page.locator('[data-rule="length"].is-met').count(), 1)
        self.assertNotEqual(meter.get_attribute('data-level'), '0')
        page.fill('#id_password2', password)
        self.submit(page, '#bnewaccount')
        return User.objects.get(username=username)

    def reloaded_signed_in(self, page):
        """Whether this browser is still signed in, after opening the home page again."""
        self.visit(page, '/')
        return bool(page.evaluate('() => window.messages && messages.username'))

    # -- signing up ----------------------------------------------------------

    def test_sign_up_confirm_and_post(self):
        # The 'post' mode: new members sign in at once and confirm before posting.
        self.set_setting(settings.EMAIL_CONFIRMATION_MODE, 'post')
        for n, (device, dark) in enumerate(VIEWS):
            with self.subTest(device=device):
                page = self.open(device=device, dark=dark)
                name = 'newcomer%d' % n
                user = self.sign_up(page, name)
                self.assertTrue(user.email_confirmation_required)
                self.assertEqual(self.signed_in_as(page), name)
                welcome = self.mails('Welcome', user.email)
                self.assertEqual(len(welcome), 1)
                self.assertEqual(welcome[0][5], {})  # an account email: no unsubscribe

                # Not confirmed: the banner says so, and the question waits.
                banner = page.locator('.confirm-banner')
                self.assertIn('Confirm your email address to start posting', banner.inner_text())
                self.assertIn(user.email, banner.inner_text())
                self.visit(page, '/questions/ask/')
                page.fill('#id_title', 'How do I export survey data from %s?' % name)
                page.fill('#editor', 'I collected about 400 forms in the field. How do I get them out as CSV?')
                page.fill('#id_tags', 'collect')
                page.keyboard.press('Escape')
                self.submit(page, '#fmask input[name=ask]')
                self.assertFalse(Question.objects.filter(author=user).exists())

                # "Send it again": a new link; the one in the welcome email stops working.
                self.submit(page, '.confirm-banner button')
                self.assertIn('We sent a new confirmation link', page.content())
                confirm = self.mails('Confirm your email address', user.email)[-1]
                self.visit_gone(page, self.link(welcome[0], 'validate'))
                self.assertIn("This link doesn", page.locator('.auth-card h1').inner_text())

                # Confirmed: the waiting question is published.
                page.goto(self.url(self.link(confirm, 'validate')))
                page.wait_for_load_state('load')
                self.check_page(page)
                self.assertTrue(Question.objects.filter(author=user).exists())
                self.assertEqual(page.locator('.confirm-banner').count(), 0)
                self.assertTrue(User.objects.get(id=user.id).email_isvalid)
                self.visit(page, self.link(confirm, 'validate'))  # used: says it's done
                self.assertIn('already confirmed', page.locator('.auth-card').inner_text())

    def test_confirm_before_signing_in(self):
        # The default mode: confirm the address before signing in.
        self.assertEqual(settings.EMAIL_CONFIRMATION_MODE.value, 'signin')
        page = self.open(device='mobile', dark=True)
        user = self.sign_up(page, 'careful')
        self.assertTrue(page.url.endswith(reverse('auth_check_email')))
        self.assertIn('Check your inbox', page.locator('.auth-card h1').inner_text())
        self.assertIn('finish creating your account', page.locator('.auth-card').inner_text())
        self.assertIsNone(self.signed_in_as(page))

        # Signing in before confirming sends a new link instead.
        self.sign_in(page, 'careful', 'green-tree-river-7')
        self.assertTrue(page.url.endswith(reverse('auth_check_email')))
        self.assertIsNone(self.signed_in_as(page))

        # Signed out, a new link is asked for with the address.
        self.visit(page, reverse('send_validation_email'))
        page.fill('#id_email', user.email)
        self.submit(page, 'form:has(#id_email) [type=submit]')
        self.assertIn('Check your inbox', page.locator('.auth-card h1').inner_text())

        self.visit(page, self.link(self.mails('Confirm your email address', user.email)[-1], 'validate'))
        self.assertIn('Email address confirmed', page.locator('.auth-card h1').inner_text())
        self.submit(page, '.auth-actions a[href="/account/signin/"]')
        page.fill('#id_username', 'careful')
        page.fill('#id_password', 'green-tree-river-7')
        self.submit(page, '#blogin')
        self.assertEqual(self.signed_in_as(page), 'careful')

    # -- passwords -----------------------------------------------------------

    def test_forgot_password_reset_and_sign_in(self):
        carol = self.create_user('carol')
        elsewhere = self.open(carol)
        page = self.open()
        self.visit(page, '/account/signin/')
        self.submit(page, 'a[href="/account/tempsignin/"]')
        page.fill('#id_email', 'carol@example.com')
        self.submit(page, 'form:has(#id_email) [type=submit]')
        self.assertIn('Check your inbox', page.locator('.auth-card h1').inner_text())

        link = re.search(r'http://testserver(/account/tempsignin/\d+/[^/\s]+/)', self.mails('Reset your password')[-1][3]).group(1)
        self.visit(page, link)
        self.assertIn('Choose a new password', page.locator('.auth-card h1').inner_text())
        page.fill('#id_password1', NEW_PASSWORD)
        page.fill('#id_password2', NEW_PASSWORD)
        self.submit(page, 'form:has(#id_password1) [type=submit]')
        self.assertTrue(page.url.endswith('/account/signin/'))
        self.assertIn('Your password has been changed', page.content())
        self.assertEqual(page.input_value('#id_username'), 'carol')
        self.assertTrue(self.mails('password was changed', carol.email))

        page.fill('#id_password', NEW_PASSWORD)
        self.submit(page, '#blogin')
        self.assertEqual(self.signed_in_as(page), 'carol')
        self.assertFalse(self.reloaded_signed_in(elsewhere))
        self.visit_gone(page, link)

    def test_change_password_signs_out_the_other_session(self):
        carol = self.create_user('carol')
        laptop = self.open(carol)
        phone = self.open(carol, device='mobile', dark=True)
        self.visit(phone, reverse('user_authsettings', kwargs={'id': carol.id}))
        phone.fill('#id_oldpw', PASSWORD)
        phone.fill('#id_password1', NEW_PASSWORD)
        phone.fill('#id_password2', NEW_PASSWORD)
        self.submit(phone, '#changepw-form [type=submit]')
        self.assertIn('Your password was changed', phone.content())
        self.assertTrue(self.reloaded_signed_in(phone))
        self.visit(laptop, '/')
        self.assertFalse(self.reloaded_signed_in(laptop))
        notice = self.mails('password was changed', carol.email)
        self.assertEqual(len(notice), 1)
        self.assertNotIn(NEW_PASSWORD, notice[0][2] + notice[0][3])

    # -- email address -------------------------------------------------------

    def edit_email(self, page, user, email):
        self.visit(page, reverse('edit_user', kwargs={'id': user.id, 'slug': user.username}))
        page.fill('#id_email', email)
        self.submit(page, 'form:has(#id_email) input[type=submit]')

    def test_change_email_confirm_the_new_address(self):
        carol = self.create_user('carol')
        page = self.open(carol, device='mobile', dark=True)
        self.edit_email(page, carol, 'carol.forest@example.com')
        self.assertEqual(User.objects.get(id=carol.id).email, 'carol@example.com')
        pending = page.locator('.email-pending')
        self.assertIn('carol.forest@example.com', pending.inner_text())
        self.assertEqual(len(self.mails('is being changed', 'carol@example.com')), 1)

        self.submit(page, '.email-pending button[value=resend]')
        confirm = self.mails('Confirm your new email address', 'carol.forest@example.com')
        self.assertEqual(len(confirm), 2)
        self.visit_gone(page, self.link(confirm[0], 'email/confirm'))  # replaced by the second
        self.visit(page, self.link(confirm[-1], 'email/confirm'))
        self.assertIn('Email address changed', page.locator('.auth-card h1').inner_text())
        self.assertEqual(User.objects.get(id=carol.id).email, 'carol.forest@example.com')
        cancel = self.mails('is being changed', 'carol@example.com')[-1]
        self.visit_gone(page, self.link(cancel, 'email/cancel'))

    def test_cancel_an_email_change_from_the_old_address(self):
        carol = self.create_user('carol')
        intruder = self.open(carol)
        self.edit_email(intruder, carol, 'intruder@example.com')
        notice = self.mails('is being changed', 'carol@example.com')[-1]
        page = self.open(device='mobile', dark=True)  # carol, reading her email on her phone
        self.visit(page, self.link(notice, 'email/cancel'))
        self.assertIn('Cancel the email change?', page.locator('.auth-card h1').inner_text())
        self.submit(page, '.auth-card form [type=submit]')
        self.assertIn('Email change cancelled', page.locator('.auth-card h1').inner_text())
        self.assertEqual(User.objects.get(id=carol.id).email, 'carol@example.com')
        self.assertFalse(self.reloaded_signed_in(intruder))
        confirm = self.mails('Confirm your new email address', 'intruder@example.com')[-1]
        self.visit_gone(page, self.link(confirm, 'email/confirm'))

        # Changed her mind about a change of her own: "Keep my current address".
        own = self.open()  # signed out everywhere: signs in again
        self.sign_in(own, 'carol', PASSWORD)
        self.edit_email(own, carol, 'carol.second@example.com')
        self.submit(own, '.email-pending button[value=cancel]')
        self.assertEqual(own.locator('.email-pending').count(), 0)

    # -- signing out ---------------------------------------------------------

    def test_sign_out_with_a_post_and_everywhere(self):
        carol = self.create_user('carol')
        other = self.open(carol, device='mobile', dark=True)
        page = self.open(carol)
        self.visit(page, '/')
        self.submit(page, '#top a[href="/logout/"]')
        self.assertIn('Sign out of', page.locator('.auth-card h1').inner_text())
        self.submit(page, '#btLogout')
        self.assertIsNone(self.signed_in_as(page))
        self.assertTrue(self.reloaded_signed_in(other))

        # From the security page: every other session.
        self.sign_in(page, 'carol', PASSWORD)
        self.visit(page, reverse('user_security', kwargs={'id': carol.id}))
        self.submit(page, 'form[action="%s"] button' % reverse('auth_signout_others'))  # confirm() accepted
        self.assertIn('This one stays signed in', page.content())
        self.assertTrue(self.reloaded_signed_in(page))
        self.assertFalse(self.reloaded_signed_in(other))

        # And everywhere, from the sign-out page.
        other = self.open(device='mobile', dark=True)
        self.sign_in(other, 'carol', PASSWORD)
        self.assertTrue(self.reloaded_signed_in(other))
        self.visit(page, '/logout/')
        page.check('#signout-everywhere')
        self.submit(page, '#btLogout')
        self.assertFalse(self.reloaded_signed_in(other))

    # -- deleting the account ------------------------------------------------

    def test_delete_account(self):
        carol = self.create_user('carol')
        question = self.create_question(carol, 'How do I export Collect survey data to CSV?')
        self.create_answer(self.create_user('bob'), question)
        page = self.open(carol, device='mobile', dark=True)
        self.visit(page, reverse('user_account_data', kwargs={'id': carol.id}))
        self.assertIn('deleted-user', page.locator('.account-danger').inner_text())
        page.fill('input[name=confirm_username]', 'carol')
        page.fill('input[name=password]', 'wrong-password')
        self.submit(page, 'form:has(input[name=confirm_username]) [type=submit]')
        self.assertIn('The password is not correct', page.content())
        self.assertTrue(User.objects.get(id=carol.id).is_active)

        page.fill('input[name=confirm_username]', 'carol')
        page.fill('input[name=password]', PASSWORD)
        self.submit(page, 'form:has(input[name=confirm_username]) [type=submit]')
        self.assertIn('Your account has been deleted', page.content())
        self.assertIsNone(self.signed_in_as(page))
        last = self.mails('account has been deleted', 'carol@example.com')
        self.assertEqual(len(last), 1)

        self.visit(page, question.get_absolute_url())
        self.assertIn('deleted-user-%d' % carol.id, page.locator('#question-table, .question, body').first.inner_text())
        self.assertNotIn('carol', page.locator('body').inner_text().replace('Carol', ''))

    # -- the new pages with axe-core --------------------------------------------

    def test_pages_are_accessible(self):
        unconfirmed = self.create_user('fresh', email_isvalid=False, email_confirmation_required=True)
        carol = self.create_user('carol')
        failures = []
        for device, dark in VIEWS:
            view = '%s%s' % (device, ', dark' if dark else '')
            page = self.open(unconfirmed, device=device, dark=dark)
            for path, status in ((reverse('send_validation_email'), 200), ('/', 200),
                                 ('/account/validate/%d/nothing/' % unconfirmed.id, 410)):
                if status == 410:
                    self.visit_gone(page, path)
                else:
                    self.visit(page, path, status=status)
                failures += ['%s (%s):\n%s' % (path, view, v) for v in AccessibilityTest.axe(self, page)]
            page = self.open(device=device, dark=dark)
            self.visit(page, '/account/local/register/')
            page.fill('#id_password1', 'green-tree')
            page.locator('.password-meter').wait_for()
            for path, status in (('/account/local/register/', None), (reverse('auth_check_email'), 200),
                                 ('/account/tempsignin/%d/nothing/' % carol.id, 410)):
                if status == 410:
                    self.visit_gone(page, path)
                elif status:
                    self.visit(page, path, status=status)
                failures += ['%s (%s):\n%s' % (path, view, v) for v in AccessibilityTest.axe(self, page)]
            page = self.open(carol, device=device, dark=dark)
            for path in ('/logout/', reverse('user_security', kwargs={'id': carol.id}),
                         reverse('user_account_data', kwargs={'id': carol.id})):
                self.visit(page, path)
                failures += ['%s (%s):\n%s' % (path, view, v) for v in AccessibilityTest.axe(self, page)]
        if failures:
            self.fail('%d accessibility violations:\n\n%s' % (len(failures), '\n'.join(failures)))
