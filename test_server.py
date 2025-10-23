#!/usr/bin/env python
"""Simple test server to verify OSQA works"""
import os
import sys
import django

print("Step 1: Setting environment", flush=True)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

print("Step 2: Calling django.setup()", flush=True)
django.setup()

print("Step 3: Importing get_wsgi_application", flush=True)
from django.core.wsgi import get_wsgi_application

print("Step 4: Getting WSGI application", flush=True)
application = get_wsgi_application()

print("Step 5: Importing make_server", flush=True)
from wsgiref.simple_server import make_server

print("=" * 80, flush=True)
print("OSQA Test Server Starting", flush=True)
print("=" * 80, flush=True)
print("Server running at: http://127.0.0.1:8000/", flush=True)
print("Press Ctrl+C to stop", flush=True)
print("=" * 80, flush=True)

print("Step 6: Creating server on port 8000", flush=True)
httpd = make_server('127.0.0.1', 8000, application)

print("Step 7: Server created successfully, starting serve_forever()", flush=True)
httpd.serve_forever()
