"""Tag management for moderators (forum/views/tags.py, templates tag_edit.html,
tag_merge.html, tag_synonyms.html): merging "csv" into "export" from the tag
page on a desktop and on a phone, the old tag's address redirecting, "csv"
typed on the ask page giving "export", renaming with undo, and the synonyms
list (add, remove, an existing tag leading to the merge page)."""
from django.urls import reverse

from forum.models import Action, Question, Tag, TagSynonym

from .base import E2ETestCase


class TagManagementTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.mod = self.create_user('mod', reputation=1, is_staff=True)
        self.q1 = self.create_question(self.alice, 'Reading Collect CSV dates in R', tags='csv collect')
        self.q2 = self.create_question(self.alice, 'Exporting plots as CSV files', tags='csv export')
        self.q3 = self.create_question(self.alice, 'Exporting a survey to KML', tags='export')

    def merge_csv_into_export(self, device, dark=False):
        page = self.open(self.mod, device=device, dark=dark)
        self.visit(page, '/tags/csv/')
        with page.expect_navigation():
            page.get_by_role('link', name='Manage tag').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/tags/csv/edit/'))

        page.get_by_label('Merge into', exact=True).fill('export')
        with page.expect_navigation():
            page.get_by_role('button', name='Review merge').click()
        self.check_page(page)
        summary = page.locator('.tag-merge-summary')
        self.assertIn('2 questions will move to export.', summary.inner_text())
        self.assertIn("can't be undone", summary.inner_text())
        self.assertTrue(Tag.objects.filter(name='csv').exists())  # not before confirming

        with page.expect_navigation():
            page.get_by_role('button', name='Merge csv into export').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/tags/export/'))
        self.assertIn('csv was merged into export', page.locator('.notify').inner_text())
        self.assertIn('Also called: csv', page.locator('.tag-synonyms').inner_text())

        self.assertFalse(Tag.objects.filter(name='csv').exists())
        self.assertEqual(Tag.objects.get(name='export').used_count, 3)
        self.assertEqual(Question.objects.get(id=self.q1.id).tagnames, 'export collect')
        self.assertEqual(Question.objects.get(id=self.q2.id).tagnames, 'export')
        self.assertTrue(Action.objects.filter(action_type='mergetag', user=self.mod).exists())

        # The old tag's address leads to the new one.
        response = page.goto(self.url('/tags/csv/'))
        self.assertEqual(response.status, 200)
        self.assertTrue(page.url.endswith('/tags/export/'))
        self.assertEqual(response.request.redirected_from.url, self.url('/tags/csv/'))
        self.check_page(page)
        return page

    def test_merge_from_the_tag_page(self):
        page = self.merge_csv_into_export('desktop')
        # The management page's history lists the merge.
        self.visit(page, '/tags/export/edit/')
        self.assertIn('merged the tag csv into export', page.locator('.tag-history').inner_text())

    def test_merge_on_a_phone(self):
        self.merge_csv_into_export('mobile', dark=True)

    def test_ask_page_gives_the_tag_of_a_synonym(self):
        TagSynonym.objects.create(source_name='comma-separated', target=Tag.objects.get(name='export'))
        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        tags = page.locator('#id_tags')

        # Suggested as "comma-separated → export"; choosing it gives export.
        tags.press_sequentially('comma', delay=50)
        suggestion = page.locator('.osqa-autocomplete li:has-text("comma-separated → export")')
        suggestion.first.wait_for()
        suggestion.first.click()
        self.assertEqual(page.locator('.tag-chip .tag-chip-name').all_inner_texts(), ['export'])

        # Typed in full, it becomes export too.
        page.get_by_role('button', name='Remove tag export').click()
        tags.press_sequentially('comma-separated', delay=30)
        suggestion.first.wait_for()
        tags.press(',')
        self.assertEqual(page.locator('.tag-chip .tag-chip-name').all_inner_texts(), ['export'])
        tags.press_sequentially('collect ')

        page.fill('#id_title', 'Exporting Collect data as comma-separated files')
        page.fill('#editor', 'I need the plots as comma-separated files for a spreadsheet, how?')
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        question = Question.objects.get(title='Exporting Collect data as comma-separated files')
        self.assertEqual(question.tagnames, 'export collect')

    def test_rename_and_undo(self):
        page = self.open(self.mod, device='mobile')
        self.visit(page, reverse('tag_edit', kwargs={'tag': 'csv'}))
        page.get_by_label('New name').fill('csv-files')
        with page.expect_navigation():
            page.get_by_role('button', name='Rename tag').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/tags/csv-files/edit/'))
        self.assertEqual(Question.objects.get(id=self.q1.id).tagnames, 'csv-files collect')
        self.assertIn('renamed the tag csv to csv-files', page.locator('.tag-history').inner_text())
        self.assertIn('csv', page.locator('.tag-synonym-list').inner_text())  # the old name kept as a synonym

        with page.expect_navigation():
            page.locator('.tag-history').get_by_role('button', name='Undo').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/tags/csv/edit/'))
        self.assertEqual(Question.objects.get(id=self.q1.id).tagnames, 'csv collect')
        self.assertIn('undone', page.locator('.tag-history li.is-undone').inner_text())

    def test_synonyms_list(self):
        page = self.open(self.mod)
        self.visit(page, '/tags/')
        with page.expect_navigation():
            page.get_by_role('link', name='Synonyms').click()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/tag-synonyms/'))
        self.assertIn('No synonyms yet', page.locator('.list-empty').inner_text())

        page.get_by_label('Synonym', exact=True).fill('kml')
        page.get_by_label('Stands for the tag').fill('export')
        with page.expect_navigation():
            page.get_by_role('button', name='Add synonym').click()
        self.check_page(page)
        self.assertEqual(TagSynonym.objects.get(source_name='kml').target.name, 'export')
        row = page.locator('.tag-synonym-table tbody tr')
        self.assertEqual(row.count(), 1)
        self.assertIn('kml', row.first.inner_text())
        self.assertIn('export', row.first.inner_text())

        with page.expect_navigation():
            page.get_by_role('button', name='Remove the synonym kml').click()
        self.check_page(page)
        self.assertFalse(TagSynonym.objects.filter(source_name='kml').exists())
        self.assertEqual(page.locator('.tag-synonym-table').count(), 0)

        # An existing tag as a synonym: its questions have to be merged, after a summary.
        page.get_by_label('Synonym', exact=True).fill('csv')
        page.get_by_label('Stands for the tag').fill('export')
        with page.expect_navigation():
            page.get_by_role('button', name='Add synonym').click()
        self.check_page(page)
        self.assertIn('/tags/csv/merge/', page.url)
        self.assertIn('2 questions will move to export.', page.locator('.tag-merge-summary').inner_text())
        with page.expect_navigation():
            page.get_by_role('link', name='Cancel').click()
        self.check_page(page)
        self.assertTrue(Tag.objects.filter(name='csv').exists())
