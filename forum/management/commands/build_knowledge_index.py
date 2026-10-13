from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Updates the AI assistant's knowledge index: answered forum threads and the documentation pages "
            "crawled from Admin > AI assistance > 'Documentation to crawl'. Only new or changed text is embedded. "
            "Run it nightly from cron.")

    def add_arguments(self, parser):
        parser.add_argument('--forum-only', action='store_true', help="Skip the documentation crawl.")
        parser.add_argument('--web-only', action='store_true', help="Skip the forum threads.")

    def handle(self, forum_only=False, web_only=False, **options):
        try:
            from forum_modules.ai import knowledge
            from forum_modules.ai.client import is_configured
        except ImportError:
            raise CommandError("The ai module is disabled or its requirements are missing.")
        if not is_configured():
            raise CommandError("The ai module has no Gemini API key or Vertex AI project configured.")

        try:
            if not web_only:
                kept, embedded, removed = knowledge.sync('forum', knowledge.forum_documents())
                self.stdout.write("Forum threads: %d unchanged, %d indexed, %d removed." % (kept, embedded, removed))
            if not forum_only:
                documents = list(knowledge.web_documents())
                if documents:
                    kept, embedded, removed = knowledge.sync('web', documents)
                    self.stdout.write("Documentation chunks: %d unchanged, %d indexed, %d removed."
                                      % (kept, embedded, removed))
                else:
                    # A network problem must not empty the index.
                    self.stdout.write("Documentation: no pages could be fetched; the indexed pages were kept.")
        except RuntimeError as e:
            raise CommandError(str(e))
