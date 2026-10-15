from django.core.management.base import BaseCommand

from forum.models import Question, User, SubscriptionSettings, QuestionSubscription


class Command(BaseCommand):
    help = "Creates missing email settings for every user, and makes them follow the questions they asked."

    def handle(self, *args, **options):
        for u in User.objects.all():
            SubscriptionSettings.objects.get_or_create(user=u)
            for q in Question.objects.filter(author=u):
                if not QuestionSubscription.objects.filter(user=u, question=q).exists():
                    QuestionSubscription(user=u, question=q).save()
