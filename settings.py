# encoding:utf-8
import os.path
import sys

SITE_ID = 1

# Set SECRET_KEY in settings_local.py or the OSQA_SECRET_KEY environment variable.
SECRET_KEY = os.environ.get('OSQA_SECRET_KEY', '')

CACHE_MAX_KEY_LENGTH = 235

# Django 4.2: MIDDLEWARE_CLASSES → MIDDLEWARE
# Note: SessionMiddleware must come before CsrfViewMiddleware when using CSRF_USE_SESSIONS
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'forum.middleware.django_cookies.CookiePreHandlerMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'forum.middleware.extended_user.ExtendedUser',
    'forum.middleware.anon_user.ConnectToSessionMessagesMiddleware',
    'forum.middleware.request_utils.RequestUtils',
    'forum.middleware.cancel.CancelActionMiddleware',
    'forum.middleware.admin_messages.AdminMessagesMiddleware',
    'forum.middleware.custom_pages.CustomPagesFallbackMiddleware',
    # Note: django.middleware.transaction.TransactionMiddleware removed (deprecated in Django 1.6, removed in 1.8)
    'forum.middleware.django_cookies.CookiePostHandlerMiddleware',
]

# Django 4.2: Required setting
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

ROOT_URLCONF = 'urls'
APPEND_SLASH = True


FILE_UPLOAD_TEMP_DIR = os.path.join(os.path.dirname(__file__), 'tmp').replace('\\','/')
FILE_UPLOAD_HANDLERS = ("django.core.files.uploadhandler.MemoryFileUploadHandler",
 "django.core.files.uploadhandler.TemporaryFileUploadHandler",)
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

ALLOW_FILE_TYPES = ('.jpg', '.jpeg', '.gif', '.bmp', '.png', '.tiff')
ALLOW_MAX_FILE_SIZE = 1024 * 1024

SESSION_SERIALIZER = 'django.contrib.sessions.serializers.PickleSerializer'

# Django 4.2: CSRF token configuration
# Use session-based CSRF tokens to avoid cookie length compatibility issues
# between Django 1.6 (32 chars) and Django 4.2 (64 chars)
CSRF_USE_SESSIONS = True
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript access if needed

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

# User settings. OSQA_SETTINGS_LOCAL selects a different module, e.g. the test
# suite runs with OSQA_SETTINGS_LOCAL=settings_local_test.
import importlib
_settings_local = importlib.import_module(os.environ.get('OSQA_SETTINGS_LOCAL', 'settings_local'))
globals().update(dict((k, v) for k, v in vars(_settings_local).items() if not k.startswith('_')))

if not SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "SECRET_KEY is not set. Add SECRET_KEY = '...' to settings_local.py or set OSQA_SECRET_KEY; generate one with: "
        "python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())'")

# The file upload dialog reads its result from a hidden same-origin iframe.
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Django 4.2: TEMPLATES configuration
template_loaders = [
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
    'forum.modules.template_loader.module_templates_loader',
    'forum.skins.load_template_source',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(os.path.dirname(__file__), 'forum', 'skins').replace('\\', '/'),
        ],
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.csrf',
                'forum.context.application_settings',
                'django.contrib.messages.context_processors.messages',
                'forum.user_messages.context_processors.user_messages',
                'django.contrib.auth.context_processors.auth',
            ],
            'loaders': template_loaders if DEBUG else [
                ('django.template.loaders.cached.Loader', template_loaders)
            ],
        },
    },
]

try:
    if len(FORUM_SCRIPT_ALIAS) > 0:
        APP_URL = '%s/%s' % (APP_URL, FORUM_SCRIPT_ALIAS[:-1])
except NameError:
    pass

app_url_split = APP_URL.split("://")

APP_PROTOCOL = app_url_split[0]
APP_DOMAIN = app_url_split[1].split('/')[0]
APP_BASE_URL = '%s://%s' % (APP_PROTOCOL, APP_DOMAIN)

FORCE_SCRIPT_NAME = ''

for path in app_url_split[1].split('/')[1:]:
    FORCE_SCRIPT_NAME = FORCE_SCRIPT_NAME + '/' + path

if FORCE_SCRIPT_NAME.endswith('/'):
    FORCE_SCRIPT_NAME = FORCE_SCRIPT_NAME[:-1]

#Module system initialization
MODULES_PACKAGE = 'forum_modules'
MODULES_FOLDER = os.path.join(SITE_SRC_ROOT, MODULES_PACKAGE)

# Python 3: filter() returns iterator, convert to list
MODULE_LIST = list(filter(lambda m: getattr(m, 'CAN_USE', True), [
        __import__('forum_modules.%s' % f, globals(), locals(), ['forum_modules'])
        for f in os.listdir(MODULES_FOLDER)
        if os.path.isdir(os.path.join(MODULES_FOLDER, f)) and
           os.path.exists(os.path.join(MODULES_FOLDER, "%s/__init__.py" % f)) and
           not f in DISABLED_MODULES
]))

# Django 4.2: MIDDLEWARE_CLASSES → MIDDLEWARE
[MIDDLEWARE.extend(
        ["%s.%s" % (m.__name__, mc) for mc in getattr(m, 'MIDDLEWARE_CLASSES', [])]
                          ) for m in MODULE_LIST]

# Add module template loaders to TEMPLATES
[TEMPLATES[0]['OPTIONS']['loaders'].extend(
        ["%s.%s" % (m.__name__, tl) for tl in getattr(m, 'TEMPLATE_LOADERS', [])]
                          ) if isinstance(TEMPLATES[0]['OPTIONS']['loaders'], list) else None for m in MODULE_LIST]


INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.admin',
    'django.contrib.humanize',
    'django.contrib.sitemaps',
    'django.contrib.messages',
    'forum',
]

if DEBUG:
    try:
        import debug_toolbar
        # Django 4.2: MIDDLEWARE_CLASSES → MIDDLEWARE
        MIDDLEWARE.append('debug_toolbar.middleware.DebugToolbarMiddleware')
        INSTALLED_APPS.append('debug_toolbar')
    except:
        pass

# Django 4.2: South removed - using Django's built-in migrations
# South was for Django < 1.7, Django 4.2 has native migrations

# Try loading Gunicorn web server
try:
    import gunicorn
    INSTALLED_APPS.append('gunicorn')
except ImportError:
    pass

if not DEBUG:
    try:
        import rosetta
        INSTALLED_APPS.append('rosetta')
    except:
        pass

AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend',]
