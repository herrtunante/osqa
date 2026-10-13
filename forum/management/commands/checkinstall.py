import re
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

from django.core.management.base import BaseCommand

# (import name, distribution name) of the packages in requirements.txt
REQUIRED = [
    ('django', 'Django'),
    ('markdown', 'Markdown'),
    ('nh3', 'nh3'),
    ('html2text', 'html2text'),
]

OPTIONAL = [
    ('MySQLdb', 'mysqlclient', 'the default MySQL database'),
    ('pymemcache', 'pymemcache', "the recommended 'PyMemcacheCache' cache"),
    ('ldap', 'python-ldap', 'the ldapauth module'),
]


class Command(BaseCommand):
    help = 'Checks that the libraries, database and settings OSQA needs are in place.'

    def check_package(self, module, dist, why=None):
        try:
            import_module(module)
        except ImportError:
            if why:
                self.stdout.write('  %s not installed (only needed for %s)' % (dist, why))
            else:
                self.stdout.write(self.style.ERROR('  ERROR: %s was not found. Run: pip install -r requirements.txt' % dist))
            return
        except Exception as e:
            self.stdout.write(self.style.ERROR('  ERROR: importing %s failed: %s' % (dist, e)))
            return
        try:
            found = version(dist)
        except PackageNotFoundError:
            found = 'unknown'
        self.stdout.write('  Found %s %s - OK' % (dist, found))

    def handle(self, **options):
        self.stdout.write('Checking dependencies:')
        for module, dist in REQUIRED:
            self.check_package(module, dist)
        for module, dist, why in OPTIONAL:
            self.check_package(module, dist, why)

        self.stdout.write('\nChecking database connection:')
        try:
            from forum.models import User
            User.objects.all().count()
            self.stdout.write('  Connection OK')
        except Exception as e:
            self.stdout.write(self.style.ERROR('  There seems to be a problem with your database: %s' % e))

        from django.conf import settings

        self.stdout.write('\nChecking important settings:')
        if not re.match(r'^https?://\w+', settings.APP_URL):
            self.stdout.write(self.style.ERROR(
                '  Your APP_URL does not seem to be a valid url. Please fill this setting '
                'with the URL of your OSQA installation'))
        else:
            self.stdout.write('  APP_URL - %s' % settings.APP_URL)
            self.stdout.write('  APP_BASE_URL - %s' % settings.APP_BASE_URL)
