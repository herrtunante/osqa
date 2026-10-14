"""Working on posts beyond asking and answering: revisions and rollback,
answer links, awarding points, liking and editing comments, community wiki,
converting between comments, answers and questions, suggestions on the ask
page, following a question, tag preferences, tag descriptions, and a question
written before signing in."""
from django.urls import reverse

from forum import settings
from forum.models import Answer, Comment, MarkedTag, Question, QuestionSubscription, Tag, User
from forum.models.action import Action

from .base import E2ETestCase, PASSWORD


class ContentTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)   # asks
        self.bob = self.create_user('bob', reputation=500)       # answers
        self.carol = self.create_user('carol', reputation=500)   # comments, likes, awards
        self.mod = self.create_user('mod', reputation=1, is_staff=True)

    def open_menu_item(self, page, post, text):
        """Opens a post's "more" menu and clicks one of its items."""
        page.click('#node-%d-menu-trigger' % post.id)
        page.locator('#node-%d-menu-dropdown a' % post.id).filter(has_text=text).click()

    def test_revisions_and_rollback(self):
        question = self.create_question(self.alice, 'Exporting plots from Collect Earth')
        page = self.open(self.alice)
        self.visit(page, reverse('edit_question', kwargs={'id': question.id}))
        page.fill('#id_title', 'Exporting plot data from Collect Earth to KML')
        page.fill('#id_summary', 'more precise title')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)

        # the "edited" note under the question leads to the revision history
        with page.expect_navigation():
            page.click('.post-update-info-edited a[href="%s"]' % question.get_revisions_url())
        self.check_page(page)
        self.assertEqual(page.locator('#revisions .revision').count(), 2)
        self.assertIn('more precise title', page.locator('#revisions').inner_text())

        # roll back: pick revision 1 on the edit page and save it unchanged
        self.visit(page, reverse('edit_question', kwargs={'id': question.id}))
        with page.expect_navigation():
            page.select_option('#id_revision', '1')
        self.check_page(page)
        self.assertEqual(page.input_value('#id_title'), 'Exporting plots from Collect Earth')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)
        question = Question.objects.get(id=question.id)
        self.assertEqual(question.title, 'Exporting plots from Collect Earth')
        self.assertEqual(question.active_revision.revision, 1)
        self.assertTrue(Action.objects.filter(action_type='rollback', node=question).exists())

    def test_answer_link_and_award_points(self):
        question = self.create_question(self.alice, 'Which projection does SEPAL use for exports?', tags='sepal')
        answer = self.create_answer(self.bob, question, 'Exports use EPSG:4326 unless you choose another CRS.')
        page = self.open(self.carol)
        page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=self.live_server_url)
        self.visit(page, question.get_absolute_url())

        # "permanent link" shows the answer's address and copies it
        page.click('#answer-container-%d a[href="%s"]' % (
            answer.id, reverse('answer_permanent_link', kwargs={'id': answer.id})))
        page.wait_for_selector('.dialog.prompt #permanent_link_url')
        self.assertTrue(page.input_value('#permanent_link_url').endswith(answer.get_absolute_url()))
        page.click('.dialog.prompt .dialog-yes')
        page.wait_for_selector('.dialog.warning:has-text("copied to your clipboard")')
        self.wait_for_ajax(page)
        self.check_page(page)
        self.assertTrue(page.evaluate('navigator.clipboard.readText()').endswith(answer.get_absolute_url()))
        page.click('.dialog.warning .dialog-yes')

        # "award points" gives bob 20 of carol's points
        page.click('#answer-container-%d a[href="%s"]' % (answer.id, reverse(
            'award_points', kwargs={'user_id': self.bob.id, 'answer_id': answer.id})))
        page.wait_for_selector('.dialog.prompt #points_input')
        page.fill('#points_input', '20')
        page.click('.dialog.prompt .dialog-yes')
        page.wait_for_selector('.dialog.warning:has-text("You have awarded bob with 20 points")')
        self.wait_for_ajax(page)
        self.check_page(page)
        self.assertEqual(User.objects.get(id=self.bob.id).reputation, self.bob.reputation + 20)
        self.assertEqual(User.objects.get(id=self.carol.id).reputation, self.carol.reputation - 20)

    def test_like_and_edit_a_comment(self):
        question = self.create_question(self.alice, 'Collect Earth crashes when opening a project')
        comment = self.create_comment(self.carol, question, 'Which version of Collect Earth do you use?')

        # bob likes carol's comment, then takes the like back
        bob_page = self.open(self.bob)
        self.visit(bob_page, question.get_absolute_url())
        self.click_command(bob_page, '#post-%d-upvote' % comment.id)
        self.assertEqual(bob_page.locator('#post-%d-score' % comment.id).inner_text().strip(), '1')
        self.assertEqual(Comment.objects.get(id=comment.id).score, 1)
        self.click_command(bob_page, '#post-%d-upvote' % comment.id)
        self.assertEqual(Comment.objects.get(id=comment.id).score, 0)

        # carol edits her comment: the form is filled with its source
        page = self.open(self.carol)
        self.visit(page, question.get_absolute_url())
        textarea = page.locator('#comment-%d-form textarea' % question.id)
        with page.expect_response(lambda r: '/node_markdown/%d/' % comment.id in r.url):
            page.click('#comment-%d-edit' % comment.id)
        page.wait_for_function('(el) => el.value.length > 0', arg=textarea.element_handle())
        self.assertEqual(textarea.input_value(), 'Which version of Collect Earth do you use?')
        textarea.fill('Which version of Collect Earth and Java do you use?')
        page.locator('#comment-%d-form input[type=submit]' % question.id).first.click()
        page.wait_for_selector('#comment-%d:has-text("and Java")' % comment.id)
        self.wait_for_ajax(page)
        self.check_page(page)
        self.assertIn('and Java', Comment.objects.get(id=comment.id).body)

    def test_community_wiki_on_a_phone(self):
        question = self.create_question(self.alice, 'Collecting tips for Collect Earth surveys')
        page = self.open(self.alice, device='mobile', dark=True)
        self.visit(page, question.get_absolute_url())
        self.open_menu_item(page, question, 'mark as community wiki')
        self.confirm_dialog(page)
        self.assertTrue(Question.objects.get(id=question.id).nis.wiki)

        self.open_menu_item(page, question, 'cancel community wiki')
        self.confirm_dialog(page)
        self.assertFalse(Question.objects.get(id=question.id).nis.wiki)

        # following the question from the sidebar
        following = lambda: QuestionSubscription.objects.filter(user=self.bob, question=question).exists()
        bob_page = self.open(self.bob, device='mobile', dark=True)
        self.visit(bob_page, question.get_absolute_url())
        self.click_command(bob_page, '.subscription_switch')
        self.assertTrue(following())
        self.assertIn('unsubscribe me', bob_page.locator('.subscription_switch').inner_text())
        self.click_command(bob_page, '.subscription_switch')
        self.assertFalse(following())

    def test_moderator_converts_posts(self):
        question = self.create_question(self.alice, 'How do I add a new plot layout to Collect?')
        comment = self.create_comment(self.carol, question, 'Use the survey designer, under Layouts, then publish.')
        answer = self.create_answer(self.bob, question, 'This is a follow-up question, not really an answer.')
        other = self.create_answer(self.carol, question, 'Open the designer and add the layout in the plot tab.')
        page = self.open(self.mod)
        self.visit(page, question.get_absolute_url())

        # comment -> answer
        page.click('#comment-%d-convert' % comment.id)
        self.confirm_dialog(page)
        converted = Answer.objects.get(id=comment.id)
        self.assertEqual(converted.parent_id, question.id)
        self.assertIn('Use the survey designer', page.locator('#answer-container-%d' % comment.id).inner_text())

        # answer -> comment, placed under another answer
        self.open_menu_item(page, other, 'convert to comment')
        page.wait_for_selector('.dialog.prompt select[name=under]')
        page.select_option('.dialog.prompt select[name=under]', str(answer.id))
        self.confirm_dialog(page)
        moved = Comment.objects.get(id=other.id)
        self.assertEqual(moved.parent_id, answer.id)
        self.assertIn('add the layout in the plot tab', page.locator('#comment-%d' % other.id).inner_text())

        # answer -> question, through the edit form
        self.open_menu_item(page, answer, 'convert to question')
        page.wait_for_url('**/convert_to_question/%d/' % answer.id)
        self.check_page(page)
        page.fill('#id_title', 'Can a plot layout be shared between Collect surveys?')
        page.fill('#id_tags', 'collect')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.get_by_role('button', name='Save edit').click()
        self.check_page(page)
        new_question = Question.objects.get(id=answer.id)
        self.assertEqual(new_question.title, 'Can a plot layout be shared between Collect surveys?')
        self.assertIn('Can a plot layout be shared', page.locator('h1').first.inner_text())

    def test_ask_page_suggests_questions_and_tags(self):
        self.create_question(self.alice, 'Exporting Collect survey data to CSV', tags='collect export')
        page = self.open(self.bob)
        self.visit(page, '/questions/ask/')

        # similar questions appear while typing the title
        with page.expect_response(lambda r: '/questions/related_questions/' in r.url):
            page.locator('#id_title').press_sequentially('export Collect survey data', delay=20)
        page.wait_for_selector('#ask-related-questions a:has-text("Exporting Collect survey data to CSV")')

        # existing tags are suggested while typing them
        page.locator('#id_tags').press_sequentially('expo', delay=50)
        suggestion = page.locator('.osqa-autocomplete li:has-text("export")')
        suggestion.first.wait_for()
        suggestion.first.click()
        self.assertEqual(page.input_value('#id_tags').strip(), 'export')
        self.check_page(page)

    def test_tag_preferences_in_the_sidebar(self):
        self.create_question(self.alice, 'Exporting Collect survey data to CSV', tags='collect export')
        page = self.open(self.bob)
        self.visit(page, '/questions/')
        marked = lambda: list(MarkedTag.objects.filter(user=self.bob).values_list('tag__name', 'reason'))

        page.fill('#interestingTagInput', 'collect')
        page.keyboard.press('Escape')
        page.click('#interestingTagAdd')
        page.wait_for_selector('.tags.interesting .deletable-tag a:has-text("collect")')
        self.wait_for_ajax(page)
        self.assertEqual(marked(), [('collect', 'good')])

        page.fill('#ignoredTagInput', 'export')
        page.keyboard.press('Escape')
        page.click('#ignoredTagAdd')
        page.wait_for_selector('.tags.ignored .deletable-tag a:has-text("export")')
        self.wait_for_ajax(page)
        self.assertEqual(sorted(marked()), [('collect', 'good'), ('export', 'bad')])
        self.check_page(page)

        # after a reload, the preferences are listed and can be removed
        self.visit(page, '/questions/')
        page.click('#interesting-tag-collect .delete-icon')
        page.wait_for_selector('#interesting-tag-collect', state='detached')
        self.wait_for_ajax(page)
        self.assertEqual(marked(), [('export', 'bad')])
        self.check_page(page)

    def test_moderator_describes_a_tag(self):
        self.create_question(self.alice, 'Exporting Collect survey data to CSV', tags='collect')
        page = self.open(self.mod)
        self.visit(page, '/tags/collect/')
        with page.expect_navigation():
            page.click('.tag-edit-link')
        self.check_page(page)
        page.fill('#tag-description', 'Questions about **Collect**, the survey designer.')
        with page.expect_navigation():
            page.click('.tag-edit-form input[type=submit]')
        self.check_page(page)
        self.assertEqual(Tag.objects.get(name='collect').description,
                         'Questions about **Collect**, the survey designer.')
        self.assertIn('the survey designer', page.locator('.tag-description').inner_text())


class PendingQuestionTest(E2ETestCase):
    """A visitor writes a question, then signs in: it is held until they
    choose to publish or discard it."""

    def setUp(self):
        super().setUp()
        self.create_user('owner')  # the first account would become the site owner
        self.dave = self.create_user('dave', reputation=10)
        # Warn about the pending post rather than publishing it straight away.
        self.set_setting(settings.WARN_PENDING_POSTS_MINUTES, 0)

    def ask_then_sign_in(self, page, title):
        self.visit(page, '/questions/ask/')
        page.fill('#id_title', title)
        page.fill('#editor', 'I wrote this before signing in, it is long enough to be posted.')
        page.fill('#id_tags', 'collect')
        page.keyboard.press('Escape')
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        self.assertIn('/account/signin/', page.url)
        self.assertFalse(Question.objects.filter(title=title).exists())
        page.fill('#id_username', 'dave')
        page.fill('#id_password', PASSWORD)
        with page.expect_navigation():
            page.click('#blogin')
        self.check_page(page)
        page.wait_for_selector('.notify:has-text("You have a question pending submission")')

    def test_publish_pending_question(self):
        page = self.open()
        self.ask_then_sign_in(page, 'Can Collect run without an internet connection?')
        with page.expect_navigation():
            page.locator('.notify a').filter(has_text='save it').click()
        self.check_page(page)
        question = Question.objects.get(title='Can Collect run without an internet connection?')
        self.assertEqual(question.author, self.dave)
        self.assertIn(question.title, page.locator('h1').first.inner_text())

    def test_discard_pending_question(self):
        page = self.open()
        self.ask_then_sign_in(page, 'Does Collect Earth work on Linux?')
        with page.expect_navigation():
            page.locator('.notify a').filter(has_text='cancel').click()
        self.check_page(page)
        self.assertFalse(Question.objects.filter(title='Does Collect Earth work on Linux?').exists())
        # the pending post is gone: its links no longer lead anywhere
        response = page.goto(self.url(reverse('manage_pending_data', kwargs={'action': 'save'})))
        self.assertEqual(response.status, 404)
        page.problems.clear()  # the browser logs the expected 404
