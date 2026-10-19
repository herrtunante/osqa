from django.core.management.base import BaseCommand

from forum import settings
from forum.models import Notification, QuestionReminder, QuestionSubscription
from forum.notifications import notify
from forum.subscriptions import filter_subscribers
from forum.utils.mail import send_template_email
from forum.utils.support import reminder_candidates


class Command(BaseCommand):
    help = ("Reminds askers, once per question, to accept an answer when their question has had an answer from "
            "someone else for ACCEPT_REMINDER_DAYS days and none is accepted (questions from the last 60 days). "
            "Run it daily from cron.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="List the questions without reminding anyone.")

    def handle(self, dry_run=False, **options):
        if int(settings.ACCEPT_REMINDER_DAYS) <= 0:
            self.stdout.write("Accept reminders are turned off (ACCEPT_REMINDER_DAYS is 0).")
            return

        emailed = 0
        questions = reminder_candidates()
        for question in questions:
            asker = question.author
            if dry_run:
                self.stdout.write("Would remind %s about %r" % (asker.username, question.title))
                continue
            QuestionReminder.objects.create(question=question)
            notify({asker.id}, Notification.REMINDER, None, question)
            # Email only if the asker still follows the question and wants emails.
            if QuestionSubscription.objects.filter(question=question, user=asker).exists():
                recipients = filter_subscribers(type(asker).objects.filter(
                    id=asker.id, subscription_settings__enable_notifications=True))
                if recipients.exists():
                    send_template_email(list(recipients), 'notifications/acceptreminder.html',
                                        {'question': question, 'email_kind': 'question', 'email_question': question,
                                         # a summary wouldn't replace reminders
                                         'email_summary_offer': False})
                    emailed += 1

        verb = "would be reminded" if dry_run else "reminded"
        self.stdout.write("%d asker(s) %s; %d by email." % (len(questions), verb, emailed))
