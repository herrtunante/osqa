# Local settings for the browser tests (e2e/):
#   OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e
# Same as the unit test settings, but the test database is a file: the live
# server answers the browser's requests from several threads, which can't
# share an in-memory SQLite database.
import os
import os.path

from settings_local_test import *  # noqa: F401,F403
from settings_local_test import DATABASES, SITE_SRC_ROOT

# E2E_DB_NAME lets several runs work side by side.
DATABASES['default']['TEST'] = {'NAME': os.path.join(SITE_SRC_ROOT, 'tmp', os.environ.get('E2E_DB_NAME', 'test_e2e.db'))}
# The browser's parallel requests write at the same time (sessions, settings,
# last seen); SQLite allows one writer, so wait for it rather than failing, and
# take the write lock at the start of a transaction so two writers can't deadlock.
DATABASES['default'].setdefault('OPTIONS', {}).update({'timeout': 30, 'transaction_mode': 'IMMEDIATE'})
