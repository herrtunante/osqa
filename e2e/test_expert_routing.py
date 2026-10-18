"""Expert routing (forum/utils/expertise.py): a member whose answers did well
in a tag gets the bell notification for a new question in it, opens
"Questions for you" from the header tab, sees why it was suggested and
answers it; another expert says "Not for me" on a phone in dark mode and
turns suggestions off in the preferences."""
from django.urls import reverse

from forum.models import AnswerRequest, Tag, TagExpertise

from .base import E2ETestCase


class ExpertRoutingTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=500)
        self.carol = self.create_user('carol', reputation=500)
        collect = Tag.objects.create(name='collect', created_by=self.alice)
        TagExpertise.objects.create(user=self.bob, tag=collect, score=24, answers=6, accepted=3)
        TagExpertise.objects.create(user=self.carol, tag=collect, score=12, answers=3, accepted=1)
        self.question = self.create_question(self.alice, 'How do I merge repeat groups into one CSV file?')

    def test_expert_is_told_and_answers_from_questions_for_you(self):
        self.assertEqual(set(AnswerRequest.objects.values_list('user__username', flat=True)), {'bob', 'carol'})
        page = self.open(self.bob)
        self.visit(page, '/')
        self.assertIn('1 unread', page.get_attribute('#notification-bell', 'aria-label'))
        with page.expect_navigation():
            page.click('#notification-bell')
        self.check_page(page)
        item = page.locator('.inbox-item.inbox-answer_request')
        self.assertIn('You might know the answer: How do I merge repeat groups into one CSV file?', item.inner_text())
        self.assertIn("you've answered 6 questions tagged collect", item.inner_text())

        # The header tab: the suggested question comes first, with the reason.
        with page.expect_navigation():
            page.click('#nav_for_you')
        self.check_page(page)
        self.assertEqual(page.locator('#nav_for_you').get_attribute('aria-current'), 'true')
        first = page.locator('.for-you-item').first
        self.assertIn("Suggested for you: you've answered 6 questions tagged collect",
                      first.locator('.for-you-reason').inner_text())
        with page.expect_navigation():
            first.get_by_role('link', name='How do I merge repeat groups into one CSV file?').click()
        self.check_page(page)
        page.fill('#fmanswer #editor', 'Tick "Merge repeats" in the export dialog: you get a single table.')
        with page.expect_navigation():
            page.click('#fmanswer input[type=button]')
        self.check_page(page)
        self.assertEqual(AnswerRequest.objects.get(user=self.bob).state, AnswerRequest.ANSWERED)
        self.assertTrue(any('You might know the answer' in m[1] for m in self.mail_to(self.bob)))

        # Answered: it leaves the page, which now explains how questions get here.
        self.visit(page, reverse('questions_for_you'))
        self.assertEqual(page.locator('.for-you-item').count(), 0)
        self.assertIn('No open questions in your tags right now', page.locator('.list-empty').inner_text())

    def test_not_for_me_on_a_phone_and_turning_suggestions_off(self):
        page = self.open(self.carol, device='mobile', dark=True)
        self.visit(page, reverse('questions_for_you'))
        item = page.locator('.for-you-item.is-requested')
        self.assertEqual(item.count(), 1)
        with page.expect_navigation():
            item.get_by_role('button', name='Not for me').click()
        self.check_page(page)
        self.assertIn("we won't suggest that question to you again", page.content())
        self.assertEqual(page.locator('.for-you-item').count(), 0)
        self.assertEqual(AnswerRequest.objects.get(user=self.carol).state, AnswerRequest.DECLINED)

        # Preferences: "Suggest questions I could answer" is on; turn it off.
        with page.expect_navigation():
            page.locator('.for-you-prefs').get_by_role('link', name='Preferences').click()
        self.check_page(page)
        box = page.get_by_label('Suggest questions I could answer')
        self.assertTrue(box.is_checked())
        box.uncheck()
        with page.expect_navigation():
            page.get_by_role('button', name='Save').click()
        self.check_page(page)
        self.visit(page, reverse('questions_for_you'))
        self.assertIn('You turned suggestions off', page.locator('.for-you-note').inner_text())
        self.create_question(self.alice, 'Can Collect export the GPS columns too?')
        self.assertFalse(AnswerRequest.objects.filter(user=self.carol, state=AnswerRequest.PENDING).exists())
