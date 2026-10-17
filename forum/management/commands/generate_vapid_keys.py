from django.core.management.base import BaseCommand

from forum.utils import push


class Command(BaseCommand):
    help = ("Prints a new VAPID key pair for browser push notifications. Put the lines in settings_local.py "
            "(or the VAPID_* environment variables of the container). Keep the private key secret, and keep "
            "the pair: a new pair makes every device subscribe again.")

    def add_arguments(self, parser):
        parser.add_argument('--env', action='store_true', help="Print NAME=value lines for an environment file.")

    def handle(self, env=False, **options):
        public, private = push.generate_keys()
        if env:
            self.stdout.write('VAPID_PUBLIC_KEY=%s\nVAPID_PRIVATE_KEY=%s\nVAPID_SUBJECT=mailto:admin@example.org\n'
                              % (public, private))
        else:
            self.stdout.write("VAPID_PUBLIC_KEY = '%s'\nVAPID_PRIVATE_KEY = '%s'\n"
                              "VAPID_SUBJECT = 'mailto:admin@example.org'  # who push services can contact\n"
                              % (public, private))
