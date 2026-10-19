import os

from django.core.management.base import BaseCommand, CommandError

from forum.utils import backup


class Command(BaseCommand):
    help = ("Restores a backup written by backup_site: REPLACES everything in the database with the archive's "
            "contents and writes its uploaded files into UPFILES_FOLDER. Check the archive with --dry-run first; "
            "the restore itself needs --confirm. A safety backup of the current site is written first.")

    def add_arguments(self, parser):
        parser.add_argument('archive', help="Path of the osqa-backup-*.tar.gz file.")
        parser.add_argument('--dry-run', action='store_true', help="Check the archive and show what it holds; change nothing.")
        parser.add_argument('--confirm', action='store_true', help="Really replace the current site's data.")
        parser.add_argument('--no-uploads', action='store_true', help="Restore the database only.")
        parser.add_argument('--uploads-dir', help="Uploaded files directory (default: the UPFILES_FOLDER setting).")
        parser.add_argument('--no-safety-backup', action='store_true',
                            help="Don't back up the current site before restoring.")
        parser.add_argument('--backup-dir', help="Where the safety backup goes (default: BACKUP_DIR, or backups/).")

    def handle(self, archive, dry_run=False, confirm=False, no_uploads=False, uploads_dir=None,
               no_safety_backup=False, backup_dir=None, **options):
        try:
            manifest = backup.inspect_backup(archive)
        except backup.BackupError as e:
            raise CommandError(str(e))

        counts = ', '.join('%s %s' % (v, k) for k, v in manifest.get('counts', {}).items())
        self.stdout.write("Backup of %s from a %s database (forum migration %s): %s; %d uploaded file(s)." % (
            manifest.get('created'), manifest.get('database_engine'), manifest.get('forum_migration'), counts,
            manifest['upload_files']))
        current = backup._last_migration()
        if manifest.get('forum_migration') and current and manifest['forum_migration'] != current:
            self.stdout.write(self.style.WARNING(
                "The backup was made at migration %s and this database is at %s: restore it with the code version "
                "it came from, then update and migrate." % (manifest['forum_migration'], current)))

        if dry_run:
            self.stdout.write("Dry run: nothing was changed. Run again with --confirm to replace ALL current data%s." % (
                '' if no_uploads else ' and write the uploaded files into %s' % (uploads_dir or backup.default_uploads_dir())))
            return
        if not confirm:
            raise CommandError("Restoring replaces ALL data of this site. Check the archive with --dry-run, then "
                               "run again with --confirm.")

        if not no_safety_backup:
            path, _ = backup.create_backup(backup_dir, uploads_dir, include_uploads=not no_uploads, keep=0,
                                           prefix='osqa-pre-restore-')
            self.stdout.write("Current site saved to %s first." % path)
        try:
            backup.restore_backup(archive, uploads_dir, include_uploads=not no_uploads)
        except backup.BackupError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS("Restored %s. Run `manage.py rebuild_search_index` if search results "
                                             "look incomplete." % os.path.basename(archive)))
