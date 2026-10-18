"""Version-aware questions and answers (forum/utils/post_versions.py): a
moderator lists the versions of a tag on its management page, a member asks
with "Applies to version" (shown once the tag is typed), an answer written
for an older version shows the "may be outdated" notice, and a member who
may edit it confirms "Still works in 4.1" and takes it back; on a desktop and
on a phone in dark mode."""
from forum.models import Answer, NodeVersion, Question, Tag, TagVersion
from forum.utils import tags as tagtools

from .base import E2ETestCase

BODY = 'I need the survey data in R. Which menu exports it as CSV, and what about dates?'


class PostVersionsTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.mod = self.create_user('mod', reputation=1, is_staff=True)
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bob', reputation=150)
        self.rita = self.create_user('rita', reputation=2500)  # may edit others' posts
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.collect = Tag.objects.get(name='collect')

    def add_versions(self):
        self.v326 = tagtools.add_version(self.collect, '3.26', self.mod, '127.0.0.1')
        self.v41 = tagtools.add_version(self.collect, '4.1', self.mod, '127.0.0.1', current=True)

    def add_version_on_page(self, page, name, current=False):
        page.fill('#tag-version-name', name)
        if current:
            page.get_by_label('This is the current version').check()
        with page.expect_navigation():
            page.get_by_role('button', name='Add version').click()
        self.check_page(page)

    def manage_versions(self, device, dark=False):
        page = self.open(self.mod, device=device, dark=dark)
        self.visit(page, '/tags/collect/edit/')
        self.assertIn('No versions yet', page.locator('.tag-manage-section:has(#tag-versions-heading)').inner_text())
        self.add_version_on_page(page, '3.26')
        self.add_version_on_page(page, '4.1', current=True)
        self.add_version_on_page(page, '4.0')
        self.assertIn('Version 4.0 added to collect.', page.locator('.notify').inner_text())
        # 4.0 goes before 4.1.
        with page.expect_navigation():
            page.get_by_role('button', name='Move 4.0 earlier').click()
        self.check_page(page)
        self.assertEqual(page.locator('.tag-version-list .version-chip').all_inner_texts(), ['3.26', '4.0', '4.1'])
        current = page.locator('.tag-version-list li.is-current')
        self.assertIn('4.1', current.inner_text())
        self.assertIn('current version', current.inner_text())
        # A typo is refused with the reason.
        self.add_version_on_page(page, '4 1')
        self.assertIn('Enter a version', page.locator('.tag-edit-error').inner_text())
        self.assertEqual(list(TagVersion.objects.filter(tag=self.collect).order_by('position').values_list(
            'name', flat=True)), ['3.26', '4.0', '4.1'])
        self.assertIn('moved the version 4.0 of the tag collect', page.locator('.tag-history').inner_text())
        return page

    def test_moderator_adds_versions(self):
        self.manage_versions('desktop')

    def test_moderator_adds_versions_on_a_phone(self):
        self.manage_versions('mobile', dark=True)

    def test_member_asks_with_a_version(self):
        self.add_versions()
        page = self.open(self.alice)
        self.visit(page, '/questions/ask/')
        versions = page.locator('#compose-versions')
        self.assertFalse(versions.is_visible())  # no versioned tag yet
        page.locator('#id_tags').press_sequentially('collect csv ')
        versions.wait_for(state='visible')
        select = page.get_by_label('collect', exact=True)
        self.assertEqual(select.locator('option').all_inner_texts(), ['Not specified', '4.1 (current)', '3.26'])
        select.select_option(label='4.1 (current)')
        with page.expect_response(lambda r: '/questions/related_questions/' in r.url):
            page.fill('#id_title', 'Reading CSV dates exported from Collect 4.1')
        page.fill('#editor', BODY)
        with page.expect_navigation():
            page.click('#fmask input[name=ask]')
        self.check_page(page)
        question = Question.objects.get(title='Reading CSV dates exported from Collect 4.1')
        self.assertEqual(NodeVersion.objects.get(node=question).version_id, self.v41.id)
        self.assertEqual(page.locator('#question-tags .version-chip').inner_text(), 'collect 4.1')

        # Removing the tag hides its select again.
        self.visit(page, '/questions/ask/')
        page.locator('#id_tags').press_sequentially('collect ')
        versions.wait_for(state='visible')
        page.get_by_role('button', name='Remove tag collect').click()
        versions.wait_for(state='hidden')

    def outdated_answer_confirmed(self, device, dark=False):
        self.add_versions()
        answer = self.create_answer(self.bob, self.question, 'Use **File > Export > CSV** in the Collect designer.')
        NodeVersion.objects.create(node=answer, tag=self.collect, version=self.v326)

        # Anybody sees the notice; nobody but those who may edit gets the button.
        page = self.open(None, device=device, dark=dark)
        self.visit(page, self.question.get_absolute_url())
        notice = page.locator('#answer-container-%d .version-notice' % answer.id)
        self.assertIn('Written for collect 3.26 — the current version is 4.1. It may be outdated.', notice.inner_text())
        self.assertEqual(page.get_by_role('button', name='Still works in 4.1').count(), 0)

        page = self.open(self.rita, device=device, dark=dark)
        self.visit(page, self.question.get_absolute_url())
        with page.expect_navigation():
            page.get_by_role('button', name='Still works in 4.1').click()
        self.check_page(page)
        self.assertIn('marked as working in collect 4.1', page.locator('.notify').inner_text())
        container = page.locator('#answer-container-%d' % answer.id)
        self.assertEqual(container.locator('.version-notice').count(), 0)
        self.assertIn('Confirmed for collect 4.1 by rita on', container.locator('.version-confirmed').inner_text())
        self.assertEqual(Answer.objects.get(id=answer.id).revisions.count(), 1)  # the text isn't edited

        with page.expect_navigation():
            container.get_by_role('button', name='Undo').click()
        self.check_page(page)
        self.assertEqual(container.locator('.version-notice').count(), 1)
        self.assertIsNone(NodeVersion.objects.get(node=answer).confirmed_version_id)
        return page

    def test_outdated_answer_confirmed(self):
        self.outdated_answer_confirmed('desktop')

    def test_outdated_answer_confirmed_on_a_phone_in_dark_mode(self):
        page = self.outdated_answer_confirmed('mobile', dark=True)
        self.assertEqual(page.evaluate('document.documentElement.dataset.theme'), 'dark')
        button = page.get_by_role('button', name='Still works in 4.1')
        box = button.bounding_box()
        self.assertGreaterEqual(min(box['width'], box['height']), 24)
        # The chip and the notice keep readable colours in dark mode.
        colors = page.evaluate('''() => {
            const chip = getComputedStyle(document.querySelector('.post-versions .version-chip'));
            const notice = getComputedStyle(document.querySelector('.version-notice'));
            return [chip.color, chip.backgroundColor, notice.color, notice.backgroundColor];
        }''')
        self.assertNotIn('rgb(255, 255, 255)', colors[1::2])  # no light backgrounds
