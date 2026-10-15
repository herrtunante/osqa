#!/usr/bin/env python
"""
Import split SQL data into SQLite database
Much simpler since each INSERT is a single row
"""
import os
import django
import sqlite3
import re

# Run from the repository root: 'settings' and the importData/ and osqa.db
# paths below are resolved from the current directory.
import sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

print("="*80)
print("OSQA Data Import from Split SQL")
print("="*80)

# Connect to SQLite
print("\n[1/3] Connecting to SQLite database...")
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
print("  ✓ Connected")

# Tables to skip
skip_tables = {'django_session', 'south_migrationhistory', 'forum_mysqlftsindex'}

# Disable foreign key checks temporarily
cursor.execute("PRAGMA foreign_keys = OFF")

# Clear existing data
print("\n[2/3] Clearing existing data...")
try:
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
except Exception as e:
    print(f"  ⚠ Error clearing data: {e}")
    conn.rollback()

# Import data
print("\n[3/3] Importing data from split SQL file...")
imported_count = 0
skipped_count = 0
error_count = 0
current_table = None

with open('importData/osqa-split.sql', encoding='utf-8', errors='ignore') as f:
    line_num = 0
    batch = []
    batch_size = 1000

    for line in f:
        line_num += 1
        line = line.strip()

        if not line or line.startswith('--') or line.startswith('/*') or line.startswith('/*!'):
            continue

        if line.startswith('INSERT INTO'):
            # Extract table name
            match = re.match(r"INSERT INTO `([^`]+)`", line)
            if match:
                table = match.group(1)

                # Check if we should skip this table
                if table in skip_tables:
                    skipped_count += 1
                    if line_num % 10000 == 0:
                        print(f"  Progress: {line_num} lines, {imported_count} imported, {skipped_count} skipped, {error_count} errors...", end='\r')
                    continue

                # Show progress when we switch tables
                if current_table != table:
                    if current_table:
                        # Commit previous table's batch
                        if batch:
                            try:
                                for sql in batch:
                                    cursor.execute(sql)
                                conn.commit()
                                batch = []
                            except Exception as e:
                                print(f"\n  ⚠ Batch commit error for {current_table}: {e}")
                                conn.rollback()
                                batch = []

                    current_table = table
                    print(f"\n  → Importing {table}...")

                # Clean up MySQL-specific syntax
                sql = line
                sql = sql.replace(r"\'", "''")  # MySQL escape to SQLite escape
                sql = sql.replace('\\n', '\n')
                sql = sql.replace('\\r', '\r')
                sql = sql.replace('\\t', '\t')
                sql = sql.replace('\\"', '"')

                batch.append(sql)

                # Execute in batches for better performance
                if len(batch) >= batch_size:
                    try:
                        for sql_stmt in batch:
                            cursor.execute(sql_stmt)
                        conn.commit()
                        imported_count += len(batch)
                        batch = []
                    except sqlite3.IntegrityError:
                        # Skip integrity errors (duplicates, etc.)
                        error_count += 1
                        conn.rollback()
                        # Try inserting one by one to find the problematic ones
                        for sql_stmt in batch:
                            try:
                                cursor.execute(sql_stmt)
                                conn.commit()
                                imported_count += 1
                            except:
                                error_count += 1
                        batch = []
                    except Exception as e:
                        print(f"\n  ⚠ Batch error for {table}: {e}")
                        error_count += len(batch)
                        conn.rollback()
                        batch = []

        if line_num % 10000 == 0:
            print(f"  Progress: {line_num} lines, {imported_count} imported, {skipped_count} skipped, {error_count} errors...", end='\r')

# Commit any remaining batch
if batch:
    try:
        for sql in batch:
            cursor.execute(sql)
        conn.commit()
        imported_count += len(batch)
    except Exception as e:
        print(f"\n  ⚠ Final batch error: {e}")
        conn.rollback()

# Re-enable foreign key checks
cursor.execute("PRAGMA foreign_keys = ON")
conn.commit()
conn.close()

print(f"\n  ✓ Processed {line_num} lines")

print("\n" + "="*80)
print("✓ DATA IMPORT COMPLETED")
print("="*80)
print("\nStatistics:")
print(f"  - Lines processed: {line_num:,}")
print(f"  - Imported: {imported_count:,} rows")
print(f"  - Skipped: {skipped_count:,} rows")
print(f"  - Errors: {error_count:,}")
print("\nVerifying imported data...")

# Verify
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
cursor.execute('SELECT node_type, COUNT(*) FROM forum_node GROUP BY node_type')
nodes = cursor.fetchall()
cursor.execute('SELECT COUNT(*) FROM auth_user')
users = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*) FROM forum_tag')
tags = cursor.fetchone()[0]
conn.close()

print("\nImported data:")
print(f"  - Users: {users:,}")
print(f"  - Tags: {tags}")
if nodes:
    print("  - Nodes:")
    for node_type, count in nodes:
        print(f"    - {node_type}: {count:,}")
else:
    print("  - Nodes: 0 (check errors above)")

print("\nYou can now run the development server:")
print("  ./venv/bin/python manage.py runserver")
print("\nHomepage: http://127.0.0.1:8000/")
print("="*80)
