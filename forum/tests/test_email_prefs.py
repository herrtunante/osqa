
from django.urls import reverse

from forum.models import Question, QuestionSubscription, MarkedTag, Tag, User
from forum.utils import unsubscribe

from .base import OSQATestCase


class EmailFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', email_isvalid=True)
        self.bob = self.create_user('bob', email_isvalid=True)
        self.login(self.alice)
        self.client.post(reverse('ask'), {'title': 'How do I export survey data to CSV?',
                                          'text': 'Question body that is long enough.', 'tags': 'collect export'})
        self.question = Question.objects.get(title='How do I export survey data to CSV?')
        self.sent_mail.clear()

    def answer_as_bob(self):
        self.login(self.bob)
        self.client.post(reverse('answer', args=[self.question.id]), {'text': 'An answer that is long enough to post.'})

    def mail_to(self, user):
        return [m for m in self.sent_mail if m[0] == user]

    def prefs(self, user):
        return User.objects.get(id=user.id).subscription_settings


class NotificationEmailTest(EmailFixture):
    def test_notifications_explain_and_offer_unsubscribing(self):
        self.answer_as_bob()
        recipient, subject, html, text, media, headers = self.mail_to(self.alice)[0]
        self.assertIn('You receive this email because you follow this question.', text)
        self.assertIn('Stop emails about this question: http://testserver/unsubscribe/', text)
        self.assertIn('Unsubscribe from all emails: http://testserver/unsubscribe/', text)
        self.assertIn('Choose which emails you get: http://testserver%s' % self.alice.get_user_subscriptions_url(), text)
        self.assertIn('How email notifications work', html)
        self.assertRegex(headers['List-Unsubscribe'], r'^<http://testserver/unsubscribe/[^>]+/>$')
        self.assertEqual(headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')

    def test_account_emails_have_no_unsubscribe(self):
        self.client.logout()
        self.client.post('/account/local/register/', {
            'username': 'carol', 'email': 'carol@example.com', 'password1': 's3cret-pass',
            'password2': 's3cret-pass', 'next': '/', 'form_token': self.signup_token()})
        recipient, subject, html, text, media, headers = self.sent_mail[-1]
        self.assertEqual(recipient.username, 'carol')
        self.assertEqual(headers, {})
        self.assertNotIn('/unsubscribe/', text)

    def test_followed_tags_respect_the_master_switch_and_ignored_tags(self):
        carol = self.create_user('carol', email_isvalid=True)
        dave = self.create_user('dave', email_isvalid=True)
        erin = self.create_user('erin', email_isvalid=True)
        gis = Tag.objects.create(name='gis', created_by=self.alice)
        MarkedTag.objects.create(user=carol, tag=Tag.objects.get(name='collect'), reason='good')
        MarkedTag.objects.create(user=dave, tag=Tag.objects.get(name='collect'), reason='good')
        prefs = self.prefs(dave)
        prefs.enable_notifications = False
        prefs.save()
        # erin follows another tag and ignores one of this question's tags: no email
        MarkedTag.objects.create(user=erin, tag=gis, reason='good')
        MarkedTag.objects.create(user=erin, tag=Tag.objects.get(name='export'), reason='bad')

        self.login(self.bob)
        self.client.post(reverse('ask'), {'title': 'Another Collect question about exports',
                                          'text': 'Question body that is long enough.', 'tags': 'collect export'})
        recipients = {m[0].username for m in self.sent_mail if 'Another Collect question' in m[1]}
        self.assertEqual(recipients, {'carol'})
        text = self.mail_to(carol)[0][3]
        self.assertIn('Stop emails about new questions:', text)


class UnsubscribeLinkTest(EmailFixture):
    def link(self, scope, question=None):
        return unsubscribe.url(self.alice, scope, question).replace('http://testserver', '')

    def test_link_asks_before_unsubscribing(self):
        self.client.logout()
        url = self.link('question', self.question)
        page = self.client.get(url)
        self.assertContains(page, 'Stop sending emails about this question')
        self.assertContains(page, 'How do I export survey data to CSV?')
        self.assertTrue(QuestionSubscription.objects.filter(user=self.alice, question=self.question).exists())

        page = self.client.post(url, {'action': 'unsubscribe'})
        self.assertContains(page, "You're unsubscribed")
        self.assertFalse(QuestionSubscription.objects.filter(user=self.alice, question=self.question).exists())
        self.answer_as_bob()
        self.assertEqual(self.mail_to(self.alice), [])

        self.client.post(url, {'action': 'undo'})
        self.assertTrue(QuestionSubscription.objects.filter(user=self.alice, question=self.question).exists())

    def test_one_click_from_the_mail_program(self):
        self.client.logout()
        response = self.client.post(self.link('all'), {'List-Unsubscribe': 'One-Click'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.prefs(self.alice).enable_notifications)
        self.answer_as_bob()
        self.assertEqual(self.mail_to(self.alice), [])

    def test_scopes(self):
        for scope, check in [('new_questions', lambda p: p.new_question_watched_tags == 'n'),
                             ('mentions', lambda p: not p.notify_reply_to_comments),
                             ('members', lambda p: p.member_joins == 'n'),
                             ('digest', lambda p: not p.send_digest)]:
            self.client.post(self.link(scope), {'action': 'unsubscribe'})
            self.assertTrue(check(self.prefs(self.alice)), scope)
        self.assertTrue(self.prefs(self.alice).enable_notifications)

    def test_bad_links(self):
        self.assertEqual(self.client.get('/unsubscribe/not-a-token/').status_code, 404)
        tampered = self.link('all')[:-6] + 'abcde/'
        self.assertEqual(self.client.get(tampered).status_code, 404)

    def test_help_page(self):
        self.assertContains(self.client.get(reverse('email_help')), 'Follow a tag')


class EmailSettingsPageTest(EmailFixture):
    def url(self):
        return self.alice.get_user_subscriptions_url()

    def test_master_switch_and_all_settings_are_saved(self):
        self.login(self.alice)
        self.assertContains(self.client.get(self.url()), "You don't follow any tags yet.")
        MarkedTag.objects.create(user=self.alice, tag=Tag.objects.get(name='collect'), reason='good')
        page = self.client.get(self.url())
        self.assertContains(page, 'Email notifications are on.')
        self.assertContains(page, 'Tags you follow')
        self.assertContains(page, '>collect</a>')

        self.client.post(self.url(), {'notifications': 'off'})
        self.assertFalse(self.prefs(self.alice).enable_notifications)
        self.assertContains(self.client.get(self.url()), 'Email notifications are off.')

        # Saving the other settings no longer switches notifications back on, and the
        # checkboxes (digest, comments, ...) are now actually saved.
        self.client.post(self.url(), {'member_joins': 'n', 'new_question': 'n', 'new_question_watched_tags': 'i',
                                      'subscribed_questions': 'i', 'notify_answers': 'on'})
        prefs = self.prefs(self.alice)
        self.assertFalse(prefs.enable_notifications)
        self.assertFalse(prefs.send_digest)
        self.assertFalse(prefs.notify_reply_to_comments)
        self.assertTrue(prefs.notify_answers)

        self.client.post(self.url(), {'notifications': 'on'})
        self.assertTrue(self.prefs(self.alice).enable_notifications)


class FollowTagTest(EmailFixture):
    def test_follow_and_unfollow(self):
        tag = Tag.objects.get(name='collect')
        self.client.logout()
        page = self.client.get(tag.get_absolute_url())
        self.assertContains(page, 'to follow this tag')
        self.assertRedirects(self.client.post(reverse('follow_tag', kwargs={'tag': 'collect'})),
                             '/account/signin/?next=%s' % tag.get_absolute_url(), fetch_redirect_response=False)

        self.login(self.bob)
        prefs = self.prefs(self.bob)
        prefs.new_question_watched_tags = 'n'
        prefs.save()
        self.assertContains(self.client.get(tag.get_absolute_url()), 'Follow tag')
        self.client.post(reverse('follow_tag', kwargs={'tag': 'collect'}), {'action': 'follow'})
        self.assertTrue(MarkedTag.objects.filter(user=self.bob, tag=tag, reason='good').exists())
        self.assertEqual(self.prefs(self.bob).new_question_watched_tags, 'i')
        page = self.client.get(tag.get_absolute_url())
        self.assertContains(page, 'You follow this tag')
        self.assertContains(page, '1 follower')

        self.client.post(reverse('follow_tag', kwargs={'tag': 'collect'}), {'action': 'unfollow'})
        self.assertFalse(MarkedTag.objects.filter(user=self.bob, tag=tag).exists())
