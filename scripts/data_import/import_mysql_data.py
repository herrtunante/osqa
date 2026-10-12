#!/usr/bin/env python
"""
Import MySQL data from SQL dump into SQLite database
Converts MySQL-specific syntax to SQLite-compatible format
"""
import os
import django
import re
import sqlite3
from datetime import datetime

# Run from the repository root: 'settings' and the importData/ and osqa.db
# paths below are resolved from the current directory.
import sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

print("="*80)
print("OSQA MySQL to SQLite Data Import")
print("="*80)

# Read the MySQL dump
print("\n[1/4] Reading MySQL dump file...")
with open('importData/osqa-20-10-2025_23-30-01.sql', 'r', encoding='utf-8', errors='ignore') as f:
    sql_content = f.read()
print("  ✓ File loaded")

# Extract INSERT statements
print("\n[2/4] Extracting INSERT statements...")
insert_pattern = r"INSERT INTO `([^`]+)` VALUES\s*(\([^;]+\));"
inserts = re.findall(insert_pattern, sql_content, re.MULTILINE | re.DOTALL)
print(f"  ✓ Found {len(inserts)} INSERT statements")

# Group by table
table_inserts = {}
for table_name, values in inserts:
    if table_name not in table_inserts:
        table_inserts[table_name] = []

    # Split multiple value sets (1,2,3),(4,5,6) into separate inserts
    # This is a simplified parser - may need adjustment for complex data
    value_sets = []
    current = ''
    paren_depth = 0

    for char in values:
        if char == '(' and paren_depth == 0:
            if current.strip():
                value_sets.append(current.strip())
            current = '('
            paren_depth = 1
        elif char == '(':
            current += char
            paren_depth += 1
        elif char == ')':
            current += char
            paren_depth -= 1
            if paren_depth == 0:
                value_sets.append(current)
                current = ''
        else:
            if paren_depth > 0:
                current += char

    if current.strip() and paren_depth == 0:
        value_sets.append(current.strip())

    table_inserts[table_name].extend(value_sets)

print(f"  ✓ Organized data by table:")
for table, data in sorted(table_inserts.items()):
    print(f"    - {table}: {len(data)} rows")

# Connect to SQLite
print("\n[3/4] Connecting to SQLite database...")
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
print("  ✓ Connected")

# Tables to import (skip django_session, south_migrationhistory, and MySQL-specific tables)
skip_tables = {'django_session', 'south_migrationhistory', 'forum_mysqlftsindex'}

# Clear existing data from relevant tables
print("\n[4/4] Importing data...")
imported_count = 0
skipped_count = 0
error_count = 0

# Order tables by dependency (auth and core tables first)
table_order = [
    'auth_user',
    'forum_user',
    'forum_tag',
    'forum_badge',
    'forum_node',
    'forum_noderevision',
    'forum_nodestate',
    'forum_node_tags',
    'forum_action',
    'forum_actionrepute',
    'forum_vote',
    'forum_award',
    'forum_flag',
    'forum_questionsubscription',
    'forum_subscriptionsettings',
    'forum_userproperty',
    'forum_authkeyuserassociation',
    'forum_validationhash',
    'forum_markedtag',
    'forum_keyvalue',
    'forum_openidassociation',
    'django_content_type',
    'auth_permission',
    'django_admin_log',
    'django_site',
]

# Add any remaining tables not in the order list
for table in table_inserts.keys():
    if table not in table_order and table not in skip_tables:
        table_order.append(table)

# Disable foreign key checks temporarily
cursor.execute("PRAGMA foreign_keys = OFF")

# Clear existing data
print("  ⚠ Clearing existing data...")
cursor.execute("DELETE FROM forum_node_tags")
cursor.execute("DELETE FROM forum_noderevision")
cursor.execute("DELETE FROM forum_nodestate")
cursor.execute("DELETE FROM forum_node")
cursor.execute("DELETE FROM forum_vote")
cursor.execute("DELETE FROM forum_action")
cursor.execute("DELETE FROM forum_tag")
cursor.execute("DELETE FROM forum_user")
cursor.execute("DELETE FROM auth_user")
cursor.execute("DELETE FROM forum_keyvalue")
conn.commit()
print("  ✓ Cleared existing data")

for table in table_order:
    if table in skip_tables:
        print(f"  ⊘ Skipping {table}")
        skipped_count += 1
        continue

    if table not in table_inserts:
        continue

    rows = table_inserts[table]
    print(f"  → Importing {table} ({len(rows)} rows)...")

    for row_values in rows:
        try:
            # Clean up the values - remove MySQL-specific syntax
            cleaned_values = row_values

            # Convert MySQL escape sequences
            cleaned_values = cleaned_values.replace(r"\'", "''")
            cleaned_values = cleaned_values.replace(r'\"', '"')

            sql = f"INSERT INTO {table} VALUES {cleaned_values}"
            cursor.execute(sql)
            imported_count += 1

        except sqlite3.IntegrityError as e:
            if 'UNIQUE constraint' in str(e):
                # Skip duplicate entries
                pass
            else:
                print(f"    ⚠ Integrity error for {table}: {e}")
                error_count += 1
        except Exception as e:
            print(f"    ⚠ Error importing row to {table}: {e}")
            print(f"      SQL: {sql[:100]}...")
            error_count += 1
            # Continue with next row
            continue

    conn.commit()
    print(f"    ✓ {table} imported")

# Re-enable foreign key checks
cursor.execute("PRAGMA foreign_keys = ON")
conn.commit()
conn.close()

print("\n" + "="*80)
print("✓ DATA IMPORT COMPLETED")
print("="*80)
print(f"\nStatistics:")
print(f"  - Imported: {imported_count} rows")
print(f"  - Skipped: {skipped_count} tables")
print(f"  - Errors: {error_count}")
print("\nYou can now run the development server:")
print("  ./venv/bin/python manage.py runserver")
print("\nHomepage: http://127.0.0.1:8000/")
print("="*80)
