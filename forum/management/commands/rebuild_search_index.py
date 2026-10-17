from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Indexes every question (title, tags, body and answers) for the site search again. Run it after "
            "upgrading or importing data; the index then follows every post as it is written, edited or deleted.")

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=500, help="Questions read per query (default 500).")

    def handle(self, batch_size=500, verbosity=1, **options):
        from forum.search import backends, index
        total = index.rebuild(batch_size=max(1, batch_size), stdout=self.stdout if verbosity > 1 else None)
        self.stdout.write("Indexed %d questions for search (%s)." % (total, backends.get_backend().name))
