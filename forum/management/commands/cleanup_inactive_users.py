import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from forum import settings
from forum.utils.antispam import dormant_users, unconfirmed_users


class Command(BaseCommand):
    help = ("Deletes dormant accounts (typically bot sign-ups): older than INACTIVE_ACCOUNT_DAYS, email never "
            "confirmed, no posts, votes or other activity, and no login in that time; and new accounts that "
            "never confirmed their email within UNCONFIRMED_ACCOUNT_DAYS. Run it daily from cron.")

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, help="Override the INACTIVE_ACCOUNT_DAYS setting (0 disables).")
        parser.add_argument('--unconfirmed-days', type=int,
                            help="Override the UNCONFIRMED_ACCOUNT_DAYS setting (0 disables).")
        parser.add_argument('--dry-run', action='store_true', help="List the accounts without deleting them.")

    def handle(self, days=None, unconfirmed_days=None, dry_run=False, verbosity=1, **options):
        days = int(settings.INACTIVE_ACCOUNT_DAYS) if days is None else days
        unconfirmed_days = int(settings.UNCONFIRMED_ACCOUNT_DAYS) if unconfirmed_days is None else unconfirmed_days

        if days <= 0:
            self.stdout.write("Dormant account cleanup is turned off (INACTIVE_ACCOUNT_DAYS is 0).")
        else:
            deleted = self._delete(dormant_users(days), dry_run, verbosity)
            if dry_run:
                self.stdout.write("%d dormant account(s) older than %d days would be deleted." % (deleted, days))
            else:
                if deleted:
                    logging.info("cleanup_inactive_users deleted %d dormant accounts", deleted)
                self.stdout.write("Deleted %d dormant account(s) older than %d days." % (deleted, days))

        if unconfirmed_days > 0:
            deleted = self._delete(unconfirmed_users(unconfirmed_days), dry_run, verbosity)
            if dry_run:
                self.stdout.write("%d unconfirmed new account(s) older than %d days would be deleted." % (
                    deleted, unconfirmed_days))
            else:
                if deleted:
                    logging.info("cleanup_inactive_users deleted %d unconfirmed accounts", deleted)
                self.stdout.write("Deleted %d unconfirmed new account(s) older than %d days." % (
                    deleted, unconfirmed_days))

    def _delete(self, users, dry_run, verbosity):
        deleted = 0
        for user in users.iterator():
            if verbosity > 1 or dry_run:
                self.stdout.write("%s %s (joined %s, %s)" % ("Would delete" if dry_run else "Deleting",
                                  user.username, user.date_joined.strftime('%Y-%m-%d'), user.email))
            if not dry_run:
                with transaction.atomic():
                    user.delete()
            deleted += 1
        return deleted
