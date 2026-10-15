"""Profile pages: the header card (avatar, name, reputation, badges, member
since and last seen as <time>, location, website, about), the tab bar
(aria-current, counts, scrolling by itself on a phone), the overview (stat
tiles, recent activity, tags, badges), empty states, the keyboard-operated
settings and moderation menu, the reputation graph in light and dark mode,
the settings pages (errors shown at the field, the section menu) and the
confirmation before deleting an account. Screenshots of each page on a
desktop and a phone, light and dark, go to tmp/e2e/profile-*.png."""
import os

from django.urls import reverse

from forum.models import User

from .base import ARTIFACTS, PASSWORD, E2ETestCase

VIEWS = (('desktop', False), ('desktop', True), ('mobile', False), ('mobile', True))


class ProfilePagesTestCase(E2ETestCase):
    def setUp(self):
        super().setUp()
        from forum.actions import AcceptAnswerAction, FavoriteAction, VoteUpAction
        self.alice = self.create_user('alice', reputation=520, real_name='Alice Forest', location='Rome, Italy',
                                      website='https://forest.example.org',
                                      about='I map **forests** with Collect and SEPAL.')
        self.bob = self.create_user('bob', reputation=180)
        self.admin = self.create_user('admin', reputation=1000, is_staff=True, is_superuser=True)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.other = self.create_question(self.alice, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')
        self.answer = self.create_answer(self.bob, self.question, 'Use the CSV export in the Collect designer.')
        self._as_request(self.alice)
        AcceptAnswerAction(user=self.alice, node=self.answer, ip='127.0.0.1').save()
        self._as_request(self.bob)
        VoteUpAction(user=self.bob, node=self.question, ip='127.0.0.1').save()
        FavoriteAction(user=self.bob, node=self.question, ip='127.0.0.1').save()
        self._as_request(self.alice)
        VoteUpAction(user=self.alice, node=self.answer, ip='127.0.0.1').save()

    def tab_url(self, name, user):
        return reverse(name, kwargs={'id': user.id, 'slug': user.username})

    def screenshot(self, page, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        path = os.path.join(ARTIFACTS, 'profile-%s.png' % name)
        page.screenshot(path=path, full_page=True)
        return path


class ProfileHeaderAndTabsTest(ProfilePagesTestCase):
    def test_header_card_seen_by_someone_else(self):
        page = self.open(self.bob)
        self.visit(page, self.alice.get_profile_url())
        card = page.locator('section.profile-card')
        self.assertEqual(card.locator('h1').inner_text().strip(), 'alice')
        self.assertIn('Alice Forest', card.inner_text())
        alice = User.objects.get(id=self.alice.id)
        self.assertEqual(page.locator('#user-reputation').inner_text().strip(), '{:,}'.format(alice.reputation))
        # member since and last seen: relative text, the exact date in <time>
        joined = card.locator('.profile-meta-joined time')
        self.assertTrue(joined.get_attribute('datetime').startswith(str(alice.date_joined.date())))
        self.assertIn('Member for', card.locator('.profile-meta-joined').inner_text())
        self.assertTrue(card.locator('.profile-meta-seen time.relative-time').get_attribute('datetime'))
        self.assertIn('Rome, Italy', card.locator('.profile-meta-location').inner_text())
        self.assertIn('forests', card.locator('.profile-about strong').inner_text())
        # the website follows the trust rules: a nofollow link, or plain text
        website = card.locator('.profile-meta-website')
        self.assertIn('https://forest.example.org', website.inner_text())
        if website.locator('a').count():
            self.assertIn('nofollow', website.locator('a').get_attribute('rel'))
        # no private details, no edit button or settings; reporting is offered
        self.assertEqual(card.locator('.profile-meta-email').count(), 0)
        self.assertEqual(card.locator('.profile-edit-button').count(), 0)
        self.assertEqual(page.locator('#user-menu').count(), 0)
        self.assertEqual(card.locator('a.profile-report-link').get_attribute('href'),
                         reverse('user_report', kwargs={'id': self.alice.id}))

    def test_owner_sees_edit_and_settings(self):
        page = self.open(self.alice)
        self.visit(page, self.alice.get_profile_url())
        card = page.locator('section.profile-card')
        self.assertIn('alice@example.com', card.locator('.profile-meta-email').inner_text())
        with page.expect_navigation():
            card.get_by_role('link', name='Edit profile').click()
        self.check_page(page)
        self.assertIn(reverse('edit_user', kwargs={'id': self.alice.id, 'slug': 'alice'}), page.url)
        self.visit(page, self.alice.get_profile_url())
        self.assertEqual(page.locator('#user-menu').inner_text().strip(), 'Settings')
        self.assertEqual(card.locator('a.profile-report-link').count(), 0)

    def test_overview_tiles_activity_tags_and_badges(self):
        page = self.open(self.bob)
        self.visit(page, self.alice.get_profile_url())
        tiles = dict(zip(page.locator('.profile-stat .profile-stat-label').all_inner_texts(),
                         page.locator('.profile-stat .profile-stat-value').all_inner_texts()))
        self.assertEqual(tiles['questions'], '2')
        self.assertEqual(tiles['answers'], '0')
        self.assertEqual(tiles['votes cast'], '1')
        self.assertEqual(tiles['reputation'], '{:,}'.format(User.objects.get(id=self.alice.id).reputation))
        # recent activity: an icon and a relative time per entry
        activity = page.locator('.profile-activity .activity-list > li')
        self.assertGreaterEqual(activity.count(), 2)
        self.assertIn(self.other.title, page.locator('.profile-activity').inner_text())
        self.assertEqual(activity.first.locator('.activity-icon').count(), 1)
        self.assertEqual(activity.first.locator('time.relative-time').count(), 1)
        # top tags link to her questions in each tag
        tags = page.locator('.profile-tag-list a.post-tag')
        self.assertIn('collect', tags.all_inner_texts())
        self.assertIn('?user=alice', tags.first.get_attribute('href'))
        self.assertGreater(page.locator('#questionsBlock .short-summary').count(), 0)

        # bob's overview: his accepted answer, the badge his first vote earned
        self.visit(page, self.bob.get_profile_url())
        tiles = dict(zip(page.locator('.profile-stat .profile-stat-label').all_inner_texts(),
                         page.locator('.profile-stat .profile-stat-value').all_inner_texts()))
        self.assertEqual(tiles['accepted answers'], '1')
        self.assertEqual(page.locator('.profile-answers .answered-accepted').count(), 1)
        self.assertGreater(page.locator('.profile-badge-grid a.medal').count(), 0)

    def test_tabs_mark_the_current_page(self):
        page = self.open(self.alice)
        self.visit(page, self.alice.get_profile_url())
        tabs = page.locator('nav.profile-tabs')
        self.assertEqual(tabs.get_attribute('aria-label'), 'Profile sections')
        self.assertEqual(tabs.locator('a#stats').get_attribute('aria-current'), 'page')
        # her votes tab, with its count
        self.assertEqual(tabs.locator('a#votes .profile-tab-count').inner_text(), '1')
        for tab in ('recent', 'reputation', 'votes', 'favorites', 'stats'):
            with page.expect_navigation():
                tabs.locator('a#%s' % tab).click()
            self.check_page(page)
            current = page.locator('nav.profile-tabs a[aria-current="page"]')
            self.assertEqual(current.count(), 1)
            self.assertEqual(current.get_attribute('id'), tab)
        # the votes tab lists the vote with its direction
        self.visit(page, self.tab_url('user_votes', self.alice))
        self.assertIn(self.question.title, page.locator('.vote-list .vote-up').inner_text())

    def test_tab_bar_scrolls_by_itself_on_a_phone(self):
        page = self.open(self.alice, device='mobile', dark=True)
        self.visit(page, self.alice.get_profile_url())  # no sideways scrolling of the page
        overflow = page.evaluate('getComputedStyle(document.querySelector(".profile-tabs .tabsA")).overflowX')
        self.assertEqual(overflow, 'auto')


class ModerationMenuTest(ProfilePagesTestCase):
    def test_keyboard_opens_the_menu_and_awards_points(self):
        before = User.objects.get(id=self.bob.id).reputation
        page = self.open(self.admin)
        self.visit(page, self.bob.get_profile_url())
        trigger = page.locator('#user-menu')
        self.assertEqual(trigger.inner_text().strip(), 'User tools')
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')
        trigger.focus()
        page.keyboard.press('Enter')
        page.wait_for_selector('#user-menu-dropdown', state='visible')
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'true')
        links = page.locator('#user-menu-dropdown a')
        page.wait_for_function("() => document.activeElement === document.querySelector('#user-menu-dropdown a')")
        page.keyboard.press('ArrowDown')
        self.assertEqual(page.evaluate('document.activeElement.textContent'), links.nth(1).inner_text())
        self.assertIn('Moderation tools', page.locator('#user-menu-dropdown li.separator').text_content())
        # Escape closes it and gives the focus back to the button
        page.keyboard.press('Escape')
        page.wait_for_selector('#user-menu-dropdown', state='hidden')
        self.assertEqual(page.evaluate('document.activeElement.id'), 'user-menu')
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')

        # opened again, End goes to the last tool; the award tool by keyboard
        page.keyboard.press('Enter')
        page.wait_for_function("() => document.activeElement === document.querySelector('#user-menu-dropdown a')")
        page.keyboard.press('End')
        self.assertEqual(page.evaluate('document.activeElement.textContent'), links.last.inner_text())
        page.locator('#award-rep-points').focus()
        page.keyboard.press('Enter')
        self.confirm_dialog(page, {'#points-to-award': '20', '#award-message': 'Thanks for the CSV answer'})
        self.assertEqual(User.objects.get(id=self.bob.id).reputation, before + 20)

    def test_suspend_dialog_asks_for_days_only_when_needed(self):
        page = self.open(self.admin, device='mobile', dark=True)
        self.visit(page, self.bob.get_profile_url())
        page.click('#user-menu')
        page.click('#user-menu-dropdown a[href="%s"]' % reverse('user_suspend', kwargs={'id': self.bob.id}))
        page.wait_for_selector('.dialog select[name=bantype]')
        self.assertTrue(page.locator('.dialog #forxdays').is_hidden())
        self.assertEqual(page.locator('.dialog label[for=suspend-publicmsg]').inner_text(), 'Public message')
        page.select_option('.dialog select[name=bantype]', 'forxdays')
        page.wait_for_selector('.dialog input[name=forxdays]', state='visible')
        self.screenshot(page, 'suspend-dialog-mobile-dark')
        page.select_option('.dialog select[name=bantype]', 'indefinitely')
        page.wait_for_selector('.dialog input[name=forxdays]', state='hidden')
        page.click('.dialog .dialog-no')
        self.check_page(page)


class ReputationGraphTest(ProfilePagesTestCase):
    def test_graph_in_light_and_dark(self):
        colours = {}
        for device, dark in (('desktop', False), ('mobile', True)):
            page = self.open(self.bob, device=device, dark=dark)
            self.visit(page, self.tab_url('user_reputation', self.alice))
            page.wait_for_selector('#diagram canvas')
            chart = page.evaluate('''() => {
                const plot = jQuery('#diagram').data('plot'), css = getComputedStyle(document.documentElement);
                const label = document.querySelector('#diagram .flot-svg text, #diagram .flot-tick-label');
                return {line: plot.getOptions().colors[0], brand: css.getPropertyValue('--brand').trim(),
                        points: plot.getData()[0].data.length, width: plot.width(),
                        label: label ? getComputedStyle(label).fill : null,
                        background: getComputedStyle(document.body).backgroundColor};
            }''')
            self.assertEqual(chart['line'], chart['brand'])  # the line in the skin's colour
            self.assertGreater(chart['points'], 0)
            self.assertGreater(chart['width'], 100)
            colours[dark] = (chart['line'], chart['background'])
            self.assertGreater(page.locator('.reputation-list .positiveReputation').count(), 0)
            self.screenshot(page, 'reputation-%s-%s' % (device, 'dark' if dark else 'light'))
        self.assertNotEqual(colours[False], colours[True])  # dark mode has its own colours

    def test_no_graph_without_reputation_changes(self):
        dana = self.create_user('dana')
        page = self.open(dana)
        self.visit(page, self.tab_url('user_reputation', dana))
        self.assertEqual(page.locator('#diagram').count(), 0)
        self.assertIn('No reputation changes yet.', page.locator('.reputation-graph').inner_text())


class EmptyProfileTest(E2ETestCase):
    def test_empty_states_for_the_owner_and_for_others(self):
        dana = self.create_user('dana')
        page = self.open(dana, device='mobile', dark=True)
        self.visit(page, dana.get_profile_url())
        text = page.locator('.profile-overview').inner_text()
        self.assertIn('No activity yet.', text)
        self.assertIn("You haven't asked any questions yet.", text)
        self.assertIn("You haven't answered any questions yet.", text)
        self.assertIn('No badges yet.', text)
        self.assertIn('No tags yet.', text)
        self.assertEqual(page.locator('#questionsBlock .profile-empty a').get_attribute('href'), reverse('ask'))
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'profile-empty-mobile-dark.png'), full_page=True)
        for name, expected in (('user_favorites', "You haven't marked any questions as favorite yet."),
                               ('user_votes', "You haven't voted yet."),
                               ('user_recent', 'No activity yet.')):
            self.visit(page, reverse(name, kwargs={'id': dana.id, 'slug': 'dana'}))
            self.assertIn(expected, page.locator('.profile-content').inner_text())

        other = self.open(self.create_user('erin'))
        self.visit(other, dana.get_profile_url())
        text = other.locator('.profile-overview').inner_text()
        self.assertIn('No questions yet.', text)
        self.assertIn('No answers yet.', text)
        self.assertEqual(other.locator('#questionsBlock .profile-empty a').count(), 0)
        self.visit(other, reverse('user_favorites', kwargs={'id': dana.id, 'slug': 'dana'}))
        self.assertIn('No favorite questions yet.', other.locator('.profile-content').inner_text())


class SettingsPagesTest(ProfilePagesTestCase):
    def test_edit_profile_shows_errors_at_the_field_then_saves(self):
        page = self.open(self.alice)
        self.visit(page, reverse('edit_user', kwargs={'id': self.alice.id, 'slug': 'alice'}))
        nav = page.locator('nav.settings-nav')
        self.assertEqual(nav.locator('a[aria-current="page"]').inner_text(), 'Edit profile')
        page.fill('#id_email', 'not an address')
        with page.expect_navigation():
            page.locator('form:has(#id_email) input[type=submit]').click()
        self.check_page(page)
        # the summary gets the focus; the message is next to the field and linked to it
        self.assertEqual(page.evaluate('document.activeElement.id'), 'settings-errors')
        error = page.locator('#id_email-error')
        self.assertIn('valid email', error.inner_text())
        email = page.locator('#id_email')
        self.assertEqual(email.get_attribute('aria-invalid'), 'true')
        self.assertIn('id_email-error', email.get_attribute('aria-describedby'))
        self.assertEqual(email.input_value(), 'not an address')
        self.assertEqual(User.objects.get(id=self.alice.id).email, 'alice@example.com')

        page.fill('#id_email', 'alice.forest@example.com')
        with page.expect_navigation():
            page.locator('form:has(#id_email) input[type=submit]').click()
        self.check_page(page)
        self.assertEqual(page.url, self.url(self.alice.get_profile_url()))
        self.assertEqual(User.objects.get(id=self.alice.id).email, 'alice.forest@example.com')

    def test_settings_menu_leads_to_each_section(self):
        page = self.open(self.alice, device='mobile')
        self.visit(page, self.alice.get_user_subscriptions_url())
        for name in ('Preferences', 'Sign-in and password', 'Your data and account', 'Email settings', 'Edit profile'):
            with page.expect_navigation():
                page.locator('nav.settings-nav').get_by_role('link', name=name).click()
            self.check_page(page)
            self.assertEqual(page.locator('nav.settings-nav a[aria-current="page"]').inner_text(), name)
        # an administrator changing someone's settings is told so; "your data" is the owner's only
        admin = self.open(self.admin)
        self.visit(admin, reverse('user_preferences', kwargs={'id': self.alice.id, 'slug': 'alice'}))
        self.assertIn('You are changing the settings of alice.', admin.locator('.settings-header').inner_text())
        self.assertEqual(admin.locator('nav.settings-nav').get_by_role('link', name='Your data and account').count(), 0)

    def test_password_errors_are_shown_at_the_fields(self):
        page = self.open(self.alice)
        self.visit(page, reverse('user_authsettings', kwargs={'id': self.alice.id}))
        page.fill('#id_oldpw', 'not-my-password')
        page.fill('#id_password1', 'a-new-long-password-9')
        page.fill('#id_password2', 'a-new-long-password-9')
        with page.expect_navigation():
            page.locator('form:has(#id_password1) [type=submit]').click()
        self.check_page(page)
        self.assertIn("That isn't your current password", page.locator('#id_oldpw-error').inner_text())
        self.assertEqual(page.locator('#id_oldpw').get_attribute('aria-invalid'), 'true')
        self.assertTrue(User.objects.get(id=self.alice.id).check_password(PASSWORD))

    def test_deleting_an_account_asks_for_the_username_first(self):
        dana = self.create_user('dana')
        page = self.open(dana, device='mobile', dark=True)
        self.visit(page, reverse('user_account_data', kwargs={'id': dana.id}))
        button = page.locator('form.account-delete-form .account-delete-button')
        self.assertTrue(button.is_disabled())
        self.assertIn("This can't be undone.", page.locator('.account-danger').inner_text())
        page.fill('input[name=confirm_username]', 'Dana')  # not exactly the username
        page.fill('input[name=password]', PASSWORD)
        self.assertTrue(button.is_disabled())
        page.fill('input[name=confirm_username]', 'dana')
        self.assertTrue(button.is_enabled())
        page.fill('input[name=password]', '')
        self.assertTrue(button.is_disabled())
        # a wrong password is refused by the server; the account stays
        page.fill('input[name=password]', 'wrong-password')
        with page.expect_navigation():
            button.click()
        self.check_page(page)
        self.assertIn('The password is not correct.', page.locator('body').inner_text())
        self.assertTrue(User.objects.filter(id=dana.id).exists())


class ProfileScreenshotsTest(ProfilePagesTestCase):
    """Every page in four views; check_page() catches sideways scrolling on phones."""

    def test_screenshots(self):
        pages = (
            ('own', self.alice, self.alice.get_profile_url(), None),
            ('other', self.bob, self.alice.get_profile_url(), None),
            ('menu', self.admin, self.bob.get_profile_url(), '#user-menu'),
            ('activity', self.bob, self.tab_url('user_recent', self.alice), None),
            ('votes', self.alice, self.tab_url('user_votes', self.alice), None),
            ('favorites', self.bob, self.tab_url('user_favorites', self.bob), None),
            ('edit', self.alice, reverse('edit_user', kwargs={'id': self.alice.id, 'slug': 'alice'}), None),
            ('email', self.alice, self.alice.get_user_subscriptions_url(), None),
            ('following', self.alice, self.alice.get_user_subscriptions_url() + '?tab=manage', None),
            ('preferences', self.alice, self.tab_url('user_preferences', self.alice), None),
            ('auth', self.alice, reverse('user_authsettings', kwargs={'id': self.alice.id}), None),
            ('data', self.alice, reverse('user_account_data', kwargs={'id': self.alice.id}), None),
        )
        saved = []
        for device, dark in VIEWS:
            for user in (self.alice, self.bob, self.admin):
                page = self.open(user, device=device, dark=dark)
                for name, viewer, path, click in pages:
                    if viewer != user:
                        continue
                    self.visit(page, path)
                    if click:
                        page.click(click)
                        page.wait_for_selector('#user-menu-dropdown', state='visible')
                        page.wait_for_timeout(300)
                    saved.append(self.screenshot(page, '%s-%s-%s' % (name, device, 'dark' if dark else 'light')))
                    self.check_page(page)
        self.assertEqual(len(saved), len(pages) * len(VIEWS))
