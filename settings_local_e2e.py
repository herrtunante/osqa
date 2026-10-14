# encoding:utf-8
# Local settings for the browser tests (e2e/):
#   OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e
# Same as the unit test settings, but the test database is a file: the live
# server answers the browser's requests from several threads, which can't
# share an in-memory SQLite database.
import os.path

from settings_local_test import *  # noqa: F401,F403
from settings_local_test import DATABASES, SITE_SRC_ROOT

DATABASES['default']['TEST'] = {'NAME': os.path.join(SITE_SRC_ROOT, 'tmp', 'test_e2e.db')}
