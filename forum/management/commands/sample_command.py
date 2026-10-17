from django.core.management.base import BaseCommand

from forum.models import Comment


class Command(BaseCommand):
    help = "Sample command: prints the number of comments."

    def handle(self, *args, **options):
        self.stdout.write(str(Comment.objects.count()))
