# encoding:utf-8
# Local settings for the test suite:
#   OSQA_SETTINGS_LOCAL=settings_local_test python manage.py test forum.tests
import os.path

SITE_SRC_ROOT = os.path.dirname(__file__)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler', 'level': 'ERROR'}},
    'root': {'handlers': ['console'], 'level': 'ERROR'},
}

DEBUG = False
SECRET_KEY = 'test-only-secret-key-not-for-production'
ALLOWED_HOSTS = ('testserver', 'localhost')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(SITE_SRC_ROOT, 'tmp', 'test.db'),
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

APP_URL = 'http://testserver'
TIME_ZONE = 'America/New_York'
USE_I18N = True
LANGUAGE_CODE = 'en'
OSQA_DEFAULT_SKIN = 'default'

DISABLED_MODULES = ['books', 'recaptcha', 'project_badges', 'stats']
