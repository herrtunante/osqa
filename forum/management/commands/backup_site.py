from django.core.management.base import BaseCommand, CommandError

from forum.utils import backup


class Command(BaseCommand):
    help = ("Writes a backup of the whole site (database contents via dumpdata, and the uploaded files) to a "
            "timestamped osqa-backup-YYYYMMDD-HHMMSS.tar.gz in BACKUP_DIR, and deletes the oldest backups beyond "
            "BACKUP_KEEP. Restore it with restore_site. Run it daily from cron.")

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', help="Directory for the archive (default: BACKUP_DIR, or backups/).")
        parser.add_argument('--keep', type=int,
                            help="Number of backups to keep in the directory (default: BACKUP_KEEP, 14; 0 keeps all).")
        parser.add_argument('--no-uploads', action='store_true', help="Leave the uploaded files out.")
        parser.add_argument('--uploads-dir', help="Uploaded files directory (default: the UPFILES_FOLDER setting).")

    def handle(self, output_dir=None, keep=None, no_uploads=False, uploads_dir=None, verbosity=1, **options):
        try:
            path, manifest = backup.create_backup(output_dir, uploads_dir, include_uploads=not no_uploads, keep=keep)
        except backup.BackupError as e:
            raise CommandError(str(e))
        if verbosity:
            counts = ', '.join('%s %s' % (v, k) for k, v in manifest['counts'].items())
            self.stdout.write("Backup written to %s (%s%s)." % (
                path, counts, '' if manifest['uploads'] else '; no uploaded files'))
