import datetime
from io import StringIO

from django.core.management import call_command

from forum import settings
from forum.actions import AcceptAnswerAction, CloseAction, DeleteAction
from forum.models import Answer, Notification, Question, QuestionReminder, QuestionSubscription
from forum.utils import support

from .base import OSQATestCase

NOW = datetime.datetime.now()


def ago(**kwargs):
    return NOW - datetime.timedelta(**kwargs)


class SupportFixture(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.asker = self.create_user('asker', email_isvalid=True)
        self.helper = self.create_user('helper', email_isvalid=True)
        self.staff = self.create_user('staff', is_staff=True)

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def question(self, title, asked, tags='collect', author=None):
        question = Question(author=author or self.asker, title=title, tagnames=tags, body='Body of %s, long enough.' % title)
        question.save()
        Question.objects.filter(id=question.id).update(added_at=asked)
        QuestionSubscription.objects.get_or_create(question=question, user=question.author)
        return Question.objects.get(id=question.id)

    def answer(self, question, at, author=None):
        answer = Answer(author=author or self.helper, parent=question, body='An answer that is long enough.')
        answer.save()
        Answer.objects.filter(id=answer.id).update(added_at=at)
        return Answer.objects.get(id=answer.id)


class FiguresTest(SupportFixture):
    def setUp(self):
        super().setUp()
        self.fast = self.question('Answered in 2 hours', ago(days=5))
        self.answer(self.fast, ago(days=5) + datetime.timedelta(hours=2))
        self.slow = self.question('Answered in 10 hours and accepted', ago(days=4), tags='sepal')
        accepted = self.answer(self.slow, ago(days=4) + datetime.timedelta(hours=10))
        AcceptAnswerAction(user=self.asker, node=accepted, ip='127.0.0.1').save()
        self.waiting = self.question('Nobody answered', ago(days=3))
        self.self_answered = self.question('Only the asker answered', ago(days=2))
        self.answer(self.self_answered, ago(days=1), author=self.asker)
        closed = self.question('Closed question', ago(days=6))
        CloseAction(user=self.staff, node=closed, extra='off topic', ip='127.0.0.1').save()
        deleted = self.question('Deleted question', ago(days=6))
        DeleteAction(user=self.staff, node=deleted, ip='127.0.0.1').save()
        self.question('Too old for the period', ago(days=40))

    def test_summary(self):
        figures = support.summary(support.period_questions(30))
        self.assertEqual((figures['asked'], figures['answered'], figures['unanswered'], figures['accepted']), (5, 2, 3, 1))
        self.assertEqual(figures['answered_percent'], 40)
        self.assertAlmostEqual(figures['median_hours'], 6.0, places=3)
        self.assertEqual(support.summary(support.period_questions(30, 'sepal'))['asked'], 1)

    def test_queues(self):
        waiting = [q.title for q in support.unanswered_queue(30)]
        self.assertEqual(waiting, ['Nobody answered', 'Only the asker answered'])  # oldest first, no closed or deleted
        self.assertEqual([q.title for q in support.unaccepted_queue(30)], ['Answered in 2 hours'])

    def test_dashboard_page(self):
        self.login(self.create_user('someone'))
        self.assertEqual(self.client.get('/admin/support/').status_code, 403)
        self.login(self.staff)
        page = self.client.get('/admin/support/?days=30')
        self.assertContains(page, 'Support dashboard')
        self.assertContains(page, 'Nobody answered')
        self.assertContains(page, '6.0 h')  # median time to first answer
        self.assertContains(page, '<th scope="row"><a href="?days=30&amp;tag=collect">collect</a></th>', html=True)
        self.assertNotContains(page, 'Too old for the period')
        self.assertContains(self.client.get('/admin/support/?days=0'), 'Too old for the period')
        sepal = self.client.get('/admin/support/?days=30&tag=sepal')  # one question, answered and accepted
        self.assertNotContains(sepal, 'Nobody answered')
        self.assertContains(sepal, 'Every question in this period has an answer')
        self.assertContains(self.client.get('/'), 'support queue')

    def test_product_tags_setting(self):
        self.set(settings.SUPPORT_TAGS, 'sepal, collect-earth')
        self.assertEqual(support.support_tags(), ['sepal', 'collect-earth'])

    def test_hours_format(self):
        self.assertEqual([support.format_hours(h) for h in (0.25, 3.5, 30, 60)], ['15 min', '3.5 h', '30 h', '2.5 d'])


class ReminderTest(SupportFixture):
    def run_command(self, *args):
        out = StringIO()
        call_command('remind_unaccepted', *args, stdout=out)
        return out.getvalue()

    def test_reminds_once_by_notification_and_email(self):
        due = self.question('Answered 8 days ago', ago(days=10))
        self.answer(due, ago(days=8))
        recent = self.question('Answered yesterday', ago(days=2))
        self.answer(recent, ago(days=1))
        old = self.question('Asked long ago', ago(days=90))
        self.answer(old, ago(days=89))

        self.assertIn('1 asker(s) would be reminded', self.run_command('--dry-run'))
        self.assertEqual(self.sent_mail, [])

        self.assertIn('1 asker(s) reminded; 1 by email', self.run_command())
        self.assertEqual(Notification.objects.get(user=self.asker).kind, 'reminder')
        recipient, subject, html, text, media, headers = self.sent_mail[0]
        self.assertEqual(recipient, self.asker)
        self.assertIn('Did an answer solve "Answered 8 days ago"?', subject)
        self.assertIn('Stop emails about this question', text)
        self.assertTrue(QuestionReminder.objects.filter(question=due).exists())

        self.assertIn('0 asker(s) reminded', self.run_command())
        self.assertEqual(len(self.sent_mail), 1)

    def test_no_email_when_the_asker_stopped_following(self):
        due = self.question('Answered 8 days ago', ago(days=10))
        self.answer(due, ago(days=8))
        QuestionSubscription.objects.filter(question=due, user=self.asker).delete()
        self.assertIn('1 asker(s) reminded; 0 by email', self.run_command())
        self.assertTrue(Notification.objects.filter(user=self.asker, kind='reminder').exists())

    def test_accepted_and_turned_off(self):
        done = self.question('Accepted already', ago(days=10))
        AcceptAnswerAction(user=self.asker, node=self.answer(done, ago(days=8)), ip='127.0.0.1').save()
        self.assertIn('0 asker(s) reminded', self.run_command())
        self.set(settings.ACCEPT_REMINDER_DAYS, 0)
        self.assertIn('turned off', self.run_command())
