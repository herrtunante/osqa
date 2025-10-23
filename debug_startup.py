#!/usr/bin/env python
"""Debug Django startup"""
import sys
import os

print("=" * 80)
print("DJANGO STARTUP DEBUG")
print("=" * 80)

print("\n1. Setting DJANGO_SETTINGS_MODULE environment variable...")
sys.stdout.flush()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

print("2. Importing django...")
sys.stdout.flush()
import django

print(f"3. Django version: {django.VERSION}")
sys.stdout.flush()

print("4. Loading Django settings...")
sys.stdout.flush()
from django.conf import settings as django_settings
print(f"   DEBUG = {django_settings.DEBUG}")
print(f"   DATABASE = {django_settings.DATABASES['default']['ENGINE']}")
sys.stdout.flush()

print("5. About to call django.setup()...")
print("   This initializes Django's app registry and loads all apps...")
sys.stdout.flush()

# This is where it hangs
django.setup()

print("6. ✓ django.setup() completed successfully!")
sys.stdout.flush()

print("7. Testing database connection...")
from django.db import connection
cursor = connection.cursor()
print("   ✓ Database connection works")

print("\n" + "=" * 80)
print("✓ STARTUP SUCCESSFUL")
print("=" * 80)
