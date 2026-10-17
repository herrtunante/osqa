from django.core.management.base import BaseCommand

from forum.utils import expertise


class Command(BaseCommand):
    help = ("Rebuilds every member's expertise per tag (accepted answers, votes, recent answers count more; see "
            "forum/utils/expertise.py), which decides who is asked to answer new questions and what \"Questions "
            "for you\" lists. Run it daily from cron.")

    def handle(self, **options):
        rows = expertise.refresh_all()
        self.stdout.write("%d expertise row(s) for members and tags." % rows)
