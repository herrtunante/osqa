"""The live question page (media/js/osqa.live.js, /questions/<id>/updates/):
one reader keeps the question open while others vote, comment, answer, edit
and accept in their own browsers; the reader sees the votes and comments
change in place and the new answer and the edit behind the "Show" banner.
Also the note to someone writing an answer, on a phone in dark mode, and the
edit conflict of two people saving the same post (screenshots in
tmp/e2e/live-*.png)."""
import os

from django.urls import reverse

from forum.models import Answer, Question

from .base import ARTIFACTS, E2ETestCase

FAST = 'window.osqaLiveInterval = 0.3;'


class LiveUpdatesTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)   # asks, edits, accepts
        self.bob = self.create_user('bob', reputation=1200)      # votes, comments, answers
        self.carol = self.create_user('carol', reputation=300)   # keeps the page open
        self.dave = self.create_user('dave', reputation=100)
        self.question = self.create_question(self.alice, 'How do I read Collect CSV dates as dates in R?',
                                             tags='collect r')
        self.first = self.create_answer(self.dave, self.question, 'Read the file with read.csv() first.')

    def screenshot(self, page, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'live-%s.png' % name), full_page=False)

    def reader(self, user, **options):
        page = self.open(user, **options)
        page.add_init_script(FAST)
        self.visit(page, self.question.get_absolute_url())
        return page

    def post_answer(self, page, text):
        self.visit(page, self.question.get_absolute_url())
        page.fill('#fmanswer #editor', text)
        with page.expect_navigation():
            page.click('#fmanswer input.answer-submit')
        self.check_page(page)
        return Answer.objects.filter(parent=self.question).latest('id')

    def test_votes_comments_answers_edits_and_accepts_reach_an_open_page(self):
        carol = self.reader(self.carol)
        bob = self.open(self.bob)
        self.visit(bob, self.question.get_absolute_url())

        # A vote: the count changes quietly.
        self.click_command(bob, '#post-%d-upvote' % self.first.id)
        carol.wait_for_function('id => document.getElementById("post-" + id + "-score").textContent.trim() === "1"',
                                arg=self.first.id)
        self.assertEqual(carol.inner_text('#live-banner').strip(), '')

        # A comment: inserted at once, highlighted.
        bob.click('#comment-tools-%d .add-comment-link' % self.question.id)
        form = bob.locator('#comment-%d-form' % self.question.id)
        form.locator('textarea').fill('Which version of R do you use?')
        form.locator('input[type=submit]').first.click()
        self.wait_for_ajax(bob)
        comment = carol.wait_for_selector('#comments-container-%d .comment.live-new:has-text("Which version of R")'
                                          % self.question.id)
        self.assertTrue(comment.is_visible())

        # An answer: counted in the banner, inserted by Show.
        answer = self.post_answer(bob, 'Give read.csv() the column classes: colClasses = c(survey_date = "Date").')
        carol.wait_for_selector('#live-banner .live-show')
        self.assertEqual(carol.inner_text('#live-banner .live-banner-text'), '1 new answer')
        self.assertEqual(carol.locator('#answer-container-%d' % answer.id).count(), 0)

        # An edit of the question joins it.
        alice = self.open(self.alice)
        self.visit(alice, reverse('edit_question', kwargs={'id': self.question.id}))
        alice.fill('#editor', 'The survey_date column comes out as text; I need a Date column in R.')
        with alice.expect_navigation():
            alice.get_by_role('button', name='Save edit').click()
        self.check_page(alice)
        carol.wait_for_selector('#live-banner .live-banner-text:has-text("1 edited post")')
        self.assertEqual(carol.inner_text('#live-banner .live-banner-text'), '1 new answer, 1 edited post')
        self.screenshot(carol, 'banner-desktop')

        carol.click('#live-banner .live-show')
        inserted = carol.wait_for_selector('#live-answers #answer-container-%d' % answer.id)
        carol.wait_for_function('id => document.activeElement && document.activeElement.id === "answer-container-" + id',
                                arg=answer.id)
        self.assertIn('colClasses', inserted.inner_text())
        self.assertIn('I need a Date column in R', carol.inner_text('.question-body'))
        self.assertEqual(carol.inner_text('#live-banner').strip(), '')
        self.assertIn('2 Answers', carol.inner_text('.answers-count'))
        # The inserted answer works like the others: its comment form opens.
        carol.click('#comment-tools-%d .add-comment-link' % answer.id)
        carol.wait_for_selector('#comment-%d-input' % answer.id, state='visible')

        # alice accepts the new answer: the mark appears on carol's page.
        self.visit(alice, self.question.get_absolute_url())
        self.click_command(alice, '#accept-answer-%d' % answer.id)
        carol.wait_for_selector('#answer-container-%d.accepted-answer' % answer.id)
        self.check_page(carol)

    def test_writing_an_answer_on_a_phone_in_dark_mode(self):
        carol = self.reader(self.carol, device='mobile', dark=True)
        carol.fill('#fmanswer #editor', 'My own draft: use as.Date() after reading the file.')
        bob = self.open(self.bob)
        answer = self.post_answer(bob, 'Use lubridate: ymd(data$survey_date) turns the text into dates.')

        notice = carol.wait_for_selector('#live-writing')
        self.assertIn('A new answer was posted while you were writing.', notice.inner_text())
        carol.wait_for_selector('#live-banner .live-show')
        self.screenshot(carol, 'writing-phone-dark')
        self.check_page(carol)  # no sideways scrolling with the banner and the note

        carol.click('#live-writing a')
        carol.wait_for_selector('#live-answers #answer-container-%d' % answer.id)
        self.assertEqual(carol.locator('#live-writing').count(), 0)
        self.assertEqual(carol.input_value('#fmanswer #editor'), 'My own draft: use as.Date() after reading the file.')
        self.check_page(carol)

    def test_edit_conflict_keeps_the_text_and_links_the_revisions(self):
        url = reverse('edit_question', kwargs={'id': self.question.id})
        first, second = self.open(self.alice), self.open(self.alice, device='mobile', dark=True)
        self.visit(first, url)
        self.visit(second, url)

        second.fill('#editor', 'Edited on the phone: the dates are text after read.csv().')
        with second.expect_navigation():
            second.get_by_role('button', name='Save edit').click()
        self.check_page(second)

        first.fill('#editor', 'Edited on the computer: how do I get Date columns from the CSV?')
        with first.expect_navigation():
            first.get_by_role('button', name='Save edit').click()
        self.check_page(first)
        alert = first.locator('#form-errors.compose-conflict')
        self.assertIn('This post was changed while you were editing it', alert.inner_text())
        self.assertTrue(first.evaluate('document.activeElement.id') == 'form-errors')
        self.assertEqual(first.input_value('#editor'), 'Edited on the computer: how do I get Date columns from the CSV?')
        self.assertIn('Edited on the phone', Question.objects.get(id=self.question.id).body)
        self.screenshot(first, 'conflict-desktop')

        # The revisions, with the difference, open beside the form.
        with first.context.expect_page() as revisions:
            alert.get_by_role('link', name='Compare the revisions').click()
        revisions.value.wait_for_load_state()
        self.assertIn(reverse('revisions', kwargs={'id': self.question.id}), revisions.value.url)

        # Saving again replaces the other version.
        with first.expect_navigation():
            first.get_by_role('button', name='Save edit').click()
        self.check_page(first)
        self.assertIn('Edited on the computer', Question.objects.get(id=self.question.id).body)
