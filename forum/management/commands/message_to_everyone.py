from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Formerly showed a message to every user through Django's per-user messages, "
            "which no longer exist. Kept so old cron entries fail with a clear error.")

    def handle(self, *args, **options):
        raise CommandError("Django's per-user messages (user.message_set) were removed, so this command "
                           "can't deliver anything. Use the site announcement settings or email instead.")
