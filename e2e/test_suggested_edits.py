"""Suggested edits: a member below the edit reputation suggests an edit on a
desktop (the summary is required, others then see "edit pending"); a
reviewer approves it on a phone in dark mode from the "Review suggested
edits" page reached by the header link, and the suggester is credited and
notified; a reviewer improves one; a rejection with a reason, undone and
redone from the moderation inbox."""
from django.urls import reverse

from forum.models import Question, SuggestedEdit, Tag, User

from .base import E2ETestCase

NEW_BODY = 'Open the survey in the designer, choose Export and then CSV; each repeat group becomes its own file.'


class SuggestedEditsTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.sam = self.create_user('sam', reputation=40)
        self.tom = self.create_user('tom', reputation=40)
        self.rita = self.create_user('rita', reputation=2500)
        self.moderator = self.create_user('moderator', reputation=10, is_staff=True)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')
        self.answer = self.create_answer(self.alice, self.question,
                                         'Open the survey in the designer and choose Export.')
        Tag.objects.get_or_create(name='csv', defaults={'created_by': self.alice})

    def suggest(self, page, post, text, summary='Added the CSV steps'):
        self.visit(page, self.question.get_absolute_url())
        with page.expect_navigation():
            page.click('a[href="%s"]' % reverse('suggest_edit', args=[post.id]))
        self.check_page(page)
        page.fill('#editor', text)
        page.fill('#id_summary', summary)
        with page.expect_navigation():
            page.get_by_role('button', name='Suggest edit').click()
        self.check_page(page)

    def test_member_suggests_and_reviewer_approves_on_a_phone(self):
        sam = self.open(self.sam)
        self.visit(sam, self.question.get_absolute_url())
        controls = sam.locator('#answer-container-%d' % self.answer.id)
        self.assertEqual(controls.get_by_role('link', name='suggest an edit').count(), 1)
        self.assertEqual(controls.get_by_role('link', name='edit', exact=True).count(), 0)
        with sam.expect_navigation():
            controls.get_by_role('link', name='suggest an edit').click()
        self.check_page(sam)
        self.assertIn('Your edit will be reviewed', sam.locator('.suggest-edit-note').inner_text())
        sam.fill('#editor', NEW_BODY)
        # The summary is required.
        with sam.expect_navigation():
            sam.get_by_role('button', name='Suggest edit').click()
        self.check_page(sam)
        self.assertIn('This field is required', sam.locator('#form-errors').inner_text())
        sam.fill('#id_summary', 'Added the CSV steps')
        with sam.expect_navigation():
            sam.get_by_role('button', name='Suggest edit').click()
        self.check_page(sam)
        self.assertIn('once a reviewer has approved it', sam.content())
        suggestion = SuggestedEdit.objects.get(node_id=self.answer.id)
        self.assertEqual(suggestion.status, SuggestedEdit.PENDING)
        self.assertNotIn('each repeat group', sam.locator('#answer-container-%d' % self.answer.id).inner_text())

        # Another low-reputation member sees that an edit is waiting.
        tom = self.open(self.tom)
        self.visit(tom, self.question.get_absolute_url())
        pending = tom.locator('#answer-container-%d .edit-pending' % self.answer.id)
        self.assertIn('edit pending', pending.inner_text())

        # The reviewer, on a phone in dark mode: header link, diff, approve.
        rita = self.open(self.rita, device='mobile', dark=True)
        self.visit(rita, '/')
        link = rita.locator('#suggested-edits-link')
        self.assertEqual(link.locator('.suggested-edits-count').inner_text(), '1')
        with rita.expect_navigation():
            link.click()
        self.check_page(rita)
        card = rita.locator('#suggested-edit-%d' % suggestion.id)
        self.assertIn('Added the CSV steps', card.inner_text())
        self.assertIn('sam', card.locator('.sugg-meta').inner_text())
        self.assertIn('repeat', card.locator('.sugg-diff ins').first.inner_text() + card.locator('.sugg-diff').inner_text())
        with rita.expect_navigation():
            card.get_by_role('button', name='Approve').click()
        self.check_page(rita)
        self.assertIn('The edit is approved and credited to sam.', rita.content())
        self.assertIn('No suggested edits are waiting', rita.content())

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, SuggestedEdit.APPROVED)
        self.assertEqual(User.objects.get(id=self.sam.id).reputation, 42)
        self.assertTrue(any('Your edit was approved' in m[1] for m in self.mail_to(self.sam)))
        self.visit(sam, self.question.get_absolute_url())
        self.assertIn('each repeat group', sam.locator('#answer-container-%d' % self.answer.id).inner_text())
        self.visit(sam, reverse('notifications'))
        self.assertIn('approved the edit you suggested', sam.locator('.inbox-list').inner_text())

    def test_reviewer_improves_a_suggestion(self):
        sam = self.open(self.sam)
        self.suggest(sam, self.question, 'Details of the export: I need one CSV file for R, with all the columns.')
        suggestion = SuggestedEdit.objects.get(node_id=self.question.id)
        rita = self.open(self.rita)
        self.visit(rita, reverse('suggested_edits'))
        with rita.expect_navigation():
            rita.locator('#suggested-edit-%d' % suggestion.id).get_by_role('link', name='Improve').click()
        self.check_page(rita)
        self.assertIn('You are improving an edit suggested by sam', rita.content())
        rita.fill('#editor', 'Details of the export: I need one CSV file for R (version 4), with all the columns.')
        with rita.expect_navigation():
            rita.get_by_role('button', name='Approve with my changes').click()
        self.check_page(rita)
        question = Question.objects.get(id=self.question.id)
        self.assertEqual([r.author.username for r in question.revisions.order_by('revision')], ['alice', 'sam', 'rita'])
        self.assertIn('version 4', question.body)

    def test_rejection_with_a_reason_and_undo_in_the_inbox(self):
        sam = self.open(self.sam)
        self.suggest(sam, self.question, 'Buy cheap watches at example dot com, best prices in town.',
                     summary='better')
        suggestion = SuggestedEdit.objects.get(node_id=self.question.id)

        page = self.open(self.moderator)
        self.visit(page, reverse('admin_moderation') + '?show=edits')
        card = page.locator('#suggested-edit-%d' % suggestion.id)
        self.assertIn('cheap watches', card.inner_text())
        card.locator('summary', has_text='Reject').click()
        card.get_by_label('It damages the post (spam, vandalism or nonsense)').check()
        card.get_by_label('A word for sam').fill('Please suggest edits that improve the post.')
        with page.expect_navigation():
            card.get_by_role('button', name='Reject edit').click()
        self.check_page(page)
        self.assertIn('The edit is rejected; sam will be told why.', page.content())
        suggestion.refresh_from_db()
        self.assertEqual((suggestion.status, suggestion.reason), (SuggestedEdit.REJECTED, 'vandalism'))
        mail = self.mail_to(self.sam)
        self.assertTrue(any('Your edit was not approved' in m[1] for m in mail))
        self.assertTrue(any('Please suggest edits that improve the post.' in m[3] for m in mail))

        # Undo from the inbox, then reject from the public review page (and undo there).
        with page.expect_navigation():
            page.get_by_role('button', name='Undo the last action').click()
        self.check_page(page)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, SuggestedEdit.PENDING)
        self.visit(page, reverse('suggested_edits'))
        card = page.locator('#suggested-edit-%d' % suggestion.id)
        card.locator('summary', has_text='Reject').click()
        card.get_by_label("It doesn't make the post easier to read or more useful").check()
        with page.expect_navigation():
            card.get_by_role('button', name='Reject edit').click()
        self.check_page(page)
        with page.expect_navigation():
            page.get_by_role('button', name='Undo the last review').click()
        self.check_page(page)
        self.assertIn('Undone', page.content())
        self.assertEqual(SuggestedEdit.objects.get(id=suggestion.id).status, SuggestedEdit.PENDING)

        self.visit(sam, reverse('notifications'))
        self.assertIn('rejected the edit you suggested', sam.locator('.inbox-list').inner_text())
        self.assertNotIn('cheap watches', Question.objects.get(id=self.question.id).body)
