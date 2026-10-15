"""Site backups: the database (Django's dumpdata, any database engine) and the
uploaded files in one timestamped .tar.gz; used by `manage.py backup_site`
and `manage.py restore_site`.

An archive holds:
- manifest.json: format, creation time, database engine, last applied forum
  migration, a few row counts;
- data.json: `dumpdata --all` without content types, permissions, sessions and
  the admin log (the same export as docs/upgrading-from-original-osqa.md);
- upfiles/: the contents of UPFILES_FOLDER (uploaded images, logo).

Archives contain personal data (emails, password hashes): they are written
with mode 0600 into a 0700 directory.
"""
import datetime
import json
import os
import re
import shutil
import tarfile
import tempfile

from django.conf import settings as djsettings
from django.core.management import call_command
from django.db import connection, transaction

FORMAT = 1
EXCLUDE = ['contenttypes', 'auth.Permission', 'sessions', 'admin.LogEntry']
NAME_RE = re.compile(r'^osqa-backup-(\d{8}-\d{6})(?:-(\d+))?\.tar\.gz$')


class BackupError(Exception):
    pass


def default_backup_dir():
    return getattr(djsettings, 'BACKUP_DIR', '') or os.path.join(djsettings.SITE_SRC_ROOT, 'backups')


def default_keep():
    return int(getattr(djsettings, 'BACKUP_KEEP', 14))


def default_uploads_dir():
    from forum import settings
    return str(settings.UPFILES_FOLDER)


def _counts():
    from forum.models import Node, User
    counts = {'users': User.objects.count()}
    for kind in ('question', 'answer', 'comment'):
        counts[kind + 's'] = Node.objects.filter(node_type=kind).count()
    return counts


def _last_migration():
    from django.db.migrations.recorder import MigrationRecorder
    applied = [name for app, name in MigrationRecorder(connection).applied_migrations() if app == 'forum']
    return max(applied) if applied else None


def _new_path(output_dir, prefix='osqa-backup-'):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(output_dir, '%s%s.tar.gz' % (prefix, stamp))
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(output_dir, '%s%s-%d.tar.gz' % (prefix, stamp, n))
    return path


def create_backup(output_dir=None, uploads_dir=None, include_uploads=True, keep=None, prefix='osqa-backup-'):
    """Writes a new archive; returns (path, manifest). Prunes old archives to `keep` (0 keeps all)."""
    output_dir = output_dir or default_backup_dir()
    uploads_dir = uploads_dir or default_uploads_dir()
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    path = _new_path(output_dir, prefix)
    partial = os.path.join(output_dir, '.%s.partial' % os.path.basename(path))

    manifest = {
        'format': FORMAT,
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'database_engine': connection.vendor,
        'forum_migration': _last_migration(),
        'counts': _counts(),
        'uploads': bool(include_uploads and os.path.isdir(uploads_dir)),
    }
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        data = os.path.join(tmp, 'data.json')
        call_command('dumpdata', all=True, exclude=EXCLUDE, output=data, verbosity=0)
        with open(os.path.join(tmp, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        old_umask = os.umask(0o077)
        try:
            with tarfile.open(partial, 'w:gz') as tar:
                tar.add(os.path.join(tmp, 'manifest.json'), 'manifest.json')
                tar.add(data, 'data.json')
                if manifest['uploads']:
                    # Regular files and directories only (no links or devices).
                    tar.add(uploads_dir, 'upfiles',
                            filter=lambda info: info if (info.isfile() or info.isdir()) else None)
        finally:
            os.umask(old_umask)
        os.chmod(partial, 0o600)
        os.replace(partial, path)
    if keep is None:
        keep = default_keep()
    prune(output_dir, keep)
    return path, manifest


def prune(output_dir, keep):
    """Deletes the oldest regular backups beyond `keep`; returns the deleted names."""
    if keep <= 0:
        return []
    def age(name):  # oldest first: by time stamp, then by the -2, -3 suffix of same-second backups
        match = NAME_RE.match(name)
        return match.group(1), int(match.group(2) or 1)
    names = sorted((n for n in os.listdir(output_dir) if NAME_RE.match(n)), key=age)
    removed = names[:-keep] if len(names) > keep else []
    for name in removed:
        os.remove(os.path.join(output_dir, name))
    return removed


def _safe_name(name):
    parts = name.replace('\\', '/').split('/')
    return not (name.startswith('/') or '..' in parts or ':' in parts[0])


def inspect_backup(path):
    """The manifest plus the number of uploaded files; checks the archive's layout."""
    if not os.path.isfile(path):
        raise BackupError('No such file: %s' % path)
    try:
        with tarfile.open(path, 'r:gz') as tar:
            members = tar.getmembers()
            names = {m.name for m in members}
            if 'manifest.json' not in names or 'data.json' not in names:
                raise BackupError('%s is not an OSQA backup (manifest.json or data.json missing)' % path)
            unsafe = [m.name for m in members if not _safe_name(m.name) or not (m.isfile() or m.isdir())]
            if unsafe:
                raise BackupError('The archive contains unsafe entries: %s' % ', '.join(unsafe[:5]))
            manifest = json.load(tar.extractfile('manifest.json'))
    except (tarfile.TarError, OSError, ValueError) as e:
        raise BackupError('Cannot read %s: %s' % (path, e))
    if manifest.get('format') != FORMAT:
        raise BackupError('Unsupported backup format %r' % manifest.get('format'))
    manifest['upload_files'] = sum(1 for m in members if m.isfile() and m.name.startswith('upfiles/'))
    return manifest


def restore_backup(path, uploads_dir=None, include_uploads=True):
    """Replaces the database contents with the archive's and writes its uploaded
    files into UPFILES_FOLDER (files not in the archive are left alone).
    Returns the manifest."""
    from django.core.cache import cache

    manifest = inspect_backup(path)
    uploads_dir = uploads_dir or default_uploads_dir()
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(path, 'r:gz') as tar:
        data = os.path.join(tmp, 'data.json')
        with tar.extractfile('data.json') as src, open(data, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        # One transaction where the database allows it (on MySQL, flush's
        # TRUNCATE commits by itself: restore_site takes a safety backup first).
        with transaction.atomic():
            call_command('flush', interactive=False, verbosity=0)
            call_command('loaddata', data, verbosity=0)
        # Objects and settings are cached outside the database.
        cache.clear()
        if include_uploads:
            os.makedirs(uploads_dir, exist_ok=True)
            for member in tar.getmembers():
                if not member.name.startswith('upfiles/') or not (member.isfile() or member.isdir()):
                    continue
                member.name = member.name[len('upfiles/'):]
                if member.name:
                    tar.extract(member, uploads_dir, filter='data')
    return manifest
