# Local settings for containers (docker-compose.yml, docs/k8s/): everything
# comes from environment variables, so the image needs no settings file.
# Selected with OSQA_SETTINGS_LOCAL=settings_local_docker. A variable NAME can
# also be given as NAME_FILE, the path of a file holding the value (Docker and
# Kubernetes secrets). A settings_local_extra.py next to this file, if present
# (e.g. mounted), is applied last for anything not covered here.
#
#   OSQA_SECRET_KEY        required
#   OSQA_APP_URL           https://forum.example.org (no trailing slash)
#   OSQA_ALLOWED_HOSTS     forum.example.org,www.forum.example.org (default: APP_URL's host)
#   OSQA_DB_ENGINE         mysql (default), postgresql or sqlite3
#   OSQA_DB_NAME / _USER / _PASSWORD / _HOST / _PORT
#   OSQA_MEMCACHED         memcached:11211[,other:11211]; empty: file cache in cache/
#   OSQA_TIME_ZONE         UTC; keep the old value when moving an existing forum
#   OSQA_LANGUAGE_CODE     en
#   OSQA_DISABLED_MODULES  recaptcha,project_badges
#   OSQA_TRUSTED_PROXIES, SENTRY_*, read by settings.py
#   LOG_FORMAT             json (default here) or text; LOG_LEVEL INFO
#   METRICS_TOKEN          enables /metrics
#   VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT  browser push (manage.py generate_vapid_keys --env)
#   OSQA_BACKUP_DIR        /app/backups;  OSQA_BACKUP_KEEP  14
#   OSQA_DEBUG             false
import os
import os.path
from urllib.parse import urlsplit


def _env(name, default=''):
    path = os.environ.get(name + '_FILE')
    if path:
        with open(path) as f:
            return f.read().strip()
    return os.environ.get(name, default)


def _list(name, default=''):
    return [item.strip() for item in _env(name, default).split(',') if item.strip()]


def _bool(name, default=False):
    return _env(name, 'true' if default else 'false').strip().lower() in ('1', 'true', 'yes', 'on')


SITE_SRC_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SITE_SRC_ROOT, 'log')

DEBUG = _bool('OSQA_DEBUG')
TEMPLATE_DEBUG = DEBUG
SECRET_KEY = _env('OSQA_SECRET_KEY')
ADMINS = ()
MANAGERS = ADMINS
INTERNAL_IPS = ('127.0.0.1',)

APP_URL = _env('OSQA_APP_URL', 'http://localhost:8000').rstrip('/')
ALLOWED_HOSTS = _list('OSQA_ALLOWED_HOSTS') or [urlsplit(APP_URL).hostname or 'localhost']

_engine = _env('OSQA_DB_ENGINE', 'mysql')
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.' + _engine,
        'NAME': _env('OSQA_DB_NAME', 'osqa'),
        'USER': _env('OSQA_DB_USER', 'osqa'),
        'PASSWORD': _env('OSQA_DB_PASSWORD'),
        'HOST': _env('OSQA_DB_HOST', 'db'),
        'PORT': _env('OSQA_DB_PORT'),
        'CONN_MAX_AGE': int(_env('OSQA_DB_CONN_MAX_AGE', '600')),
        'CONN_HEALTH_CHECKS': True,
    }
}
if _engine == 'mysql':
    DATABASES['default']['OPTIONS'] = {'charset': 'utf8mb4'}
elif _engine == 'sqlite3':
    DATABASES['default'].update(USER='', PASSWORD='', HOST='', PORT='', CONN_MAX_AGE=0)

_memcached = _list('OSQA_MEMCACHED')
if _memcached:
    # Short timeouts: a memcached outage makes requests slower, not stuck.
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
                          'LOCATION': _memcached,
                          'OPTIONS': {'connect_timeout': 1, 'timeout': 1, 'no_delay': True}}}
else:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
                          'LOCATION': os.path.join(SITE_SRC_ROOT, 'cache'), 'OPTIONS': {'MAX_ENTRIES': 10000}}}
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

TIME_ZONE = _env('OSQA_TIME_ZONE', 'UTC')
USE_I18N = True
LANGUAGE_CODE = _env('OSQA_LANGUAGE_CODE', 'en')
OSQA_DEFAULT_SKIN = 'default'
DISABLED_MODULES = _list('OSQA_DISABLED_MODULES', 'recaptcha,project_badges')

# Logs go to standard output (the container runtime collects them).
LOG_FORMAT = _env('LOG_FORMAT', 'json')
LOG_LEVEL = _env('LOG_LEVEL', 'INFO')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {'request': {'()': 'forum.utils.logs.RequestContextFilter'}},
    'formatters': {'default': {'format': '%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s'}},
    'handlers': {'stdout': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout',
                            'formatter': 'default', 'filters': ['request']}},
    'loggers': {'forum.security': {'handlers': ['stdout'], 'level': 'INFO', 'propagate': False}},
    'root': {'handlers': ['stdout'], 'level': LOG_LEVEL},
}

METRICS_TOKEN = _env('METRICS_TOKEN')
VAPID_PUBLIC_KEY = _env('VAPID_PUBLIC_KEY')
VAPID_PRIVATE_KEY = _env('VAPID_PRIVATE_KEY')
VAPID_SUBJECT = _env('VAPID_SUBJECT')
BACKUP_DIR = _env('OSQA_BACKUP_DIR', os.path.join(SITE_SRC_ROOT, 'backups'))
BACKUP_KEEP = int(_env('OSQA_BACKUP_KEEP', '14'))
for _name in ('SENTRY_DSN', 'SENTRY_FRONTEND_DSN'):
    if os.environ.get(_name + '_FILE'):
        globals()[_name] = _env(_name)

if os.path.exists(os.path.join(SITE_SRC_ROOT, 'settings_local_extra.py')):
    from settings_local_extra import *  # noqa: F401,F403
