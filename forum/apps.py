from django.apps import AppConfig


class ForumConfig(AppConfig):
    name = 'forum'

    def ready(self):
        # Records when the scheduled management commands last ran (/metrics).
        from forum.utils import cron
        cron.install()
