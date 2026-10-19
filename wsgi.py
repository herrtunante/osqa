"""WSGI entry point, e.g. `gunicorn wsgi:application` from the repository root."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
