import os

from django.core.management.base import BaseCommand

from forum import settings
from forum.utils import images


class Command(BaseCommand):
    help = ("Writes the AVIF/WebP copies at smaller widths and the size file of every uploaded image that "
            "lacks them (forum/utils/images.py). Safe to run again: images already done are skipped. "
            "With --rerender, posts showing uploads are rendered again so they use <picture>.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Only report what would be done.")
        parser.add_argument('--rerender', action='store_true',
                            help="Then render again the posts whose HTML shows an uploaded image.")
        parser.add_argument('--uploads-dir', help="Uploaded files directory (default: the UPFILES_FOLDER setting).")

    def handle(self, *args, **options):
        folder = options['uploads_dir'] or str(settings.UPFILES_FOLDER)
        dry_run = options['dry_run']
        if not os.path.isdir(folder):
            self.stderr.write('No uploads folder at %s' % folder)
            return
        written = done = skipped = 0
        for name in sorted(os.listdir(folder)):
            if not images.is_upload_name(name) or not os.path.isfile(os.path.join(folder, name)):
                continue
            meta, changed = images.write_variants(folder, name, dry_run=dry_run)
            if meta is None:
                skipped += 1
            elif changed:
                written += 1
                if options['verbosity'] > 1 or dry_run:
                    self.stdout.write('%s %s (%dx%d)' % ('would write' if dry_run else 'wrote', name,
                                                         meta['width'], meta['height']))
            else:
                done += 1
        self.stdout.write('%s %d image(s), %d already done, %d other file(s) skipped.' % (
            'Would write variants of' if dry_run else 'Wrote variants of', written, done, skipped))
        if options['rerender']:
            self.rerender(dry_run)

    def rerender(self, dry_run):
        from forum.models import Node
        alias = str(settings.UPFILES_ALIAS)
        changed = 0
        for node in Node.objects.filter(body__contains=alias).order_by('id'):
            source = node.active_revision.body if node.active_revision else node.body
            body = node.rendered(source)
            if body != node.body:
                changed += 1
                if not dry_run:
                    node.body = body
                    node.save()
        self.stdout.write('%s %d post(s).' % ('Would render again' if dry_run else 'Rendered again', changed))
