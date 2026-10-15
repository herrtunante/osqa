
from django.core.management.base import BaseCommand
from forum.models import Node

import logging

class Command(BaseCommand):
    help = "Renders the HTML of every post again from the Markdown of its active revision."

    def handle(self,*args, **options):
        # Re-render node bodies from their source (the active revision's
        # Markdown); `body` itself holds HTML that was already rendered.
        for node in Node.objects.all():
            try:
                source = node.active_revision.body if node.active_revision else node.body
                node.body = node.rendered(source)
                node.save()
                self.stdout.write('%d' % node.id)
            except Exception as e:
                logging.error(e)
