#!/usr/bin/env python
"""
Import original MySQL data with proper column handling
Uses two-phase approach:
1. Import all tables normally except forum_node and forum_noderevision
2. Use Django ORM to properly import forum_node and forum_noderevision with correct field mapping
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
print("OSQA Data Import - Two-Phase Approach")
print("="*80)

# Connect to SQLite
print("\n[1/4] Connecting to SQLite database...")
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
print("  ✓ Connected")

# Tables to skip in phase 1
skip_tables = {'django_session', 'south_migrationhistory', 'forum_mysqlftsindex',
               'forum_node', 'forum_noderevision'}  # Will handle these in phase 2

# Disable foreign key checks temporarily
cursor.execute("PRAGMA foreign_keys = OFF")

# Clear existing data
print("\n[2/4] Clearing existing data...")
try:
    cursor.execute("DELETE FROM forum_node_tags")
    cursor.execute("DELETE FROM forum_noderevision")
    cursor.execute("DELETE FROM forum_nodestate")
    cursor.execute("DELETE FROM forum_node")
    cursor.execute("DELETE FROM forum_vote")
    cursor.execute("DELETE FROM forum_action")
    cursor.execute("DELETE FROM forum_tag")
    conn.commit()
    print("  ✓ Cleared existing data")
except Exception as e:
    print(f"  ⚠ Error clearing data: {e}")
    conn.rollback()

# Phase 1: Import all tables except forum_node and forum_noderevision
print("\n[3/4] Phase 1: Importing regular tables...")
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
                    continue

                # Show progress when we switch tables
                if current_table != table:
                    # Commit previous table's batch
                    if current_table and batch:
                        try:
                            for sql in batch:
                                cursor.execute(sql)
                            conn.commit()
                            imported_count += len(batch)
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

                # Execute in batches
                if len(batch) >= batch_size:
                    success = 0
                    for sql_stmt in batch:
                        try:
                            cursor.execute(sql_stmt)
                            success += 1
                        except sqlite3.IntegrityError:
                            pass  # Skip duplicates
                        except Exception as e:
                            if 'UNIQUE constraint' not in str(e):
                                error_count += 1
                    conn.commit()
                    imported_count += success
                    batch = []

        if line_num % 10000 == 0:
            print(f"  Progress: {line_num:,} lines, {imported_count:,} imported...", end='\r')

# Final batch
if batch:
    for sql in batch:
        try:
            cursor.execute(sql)
            imported_count += 1
        except:
            error_count += 1
    conn.commit()

print(f"\n  ✓ Phase 1 completed: {imported_count:,} rows imported from regular tables")

# Phase 2: Import forum_node and forum_noderevision using Django ORM
print("\n[4/4] Phase 2: Importing nodes using Django ORM with column mapping...")

# Parse forum_node and forum_noderevision from SQL file
print("  → Parsing node data from SQL file...")

# Column mappings
NODE_COLS_MYSQL = [
    'id', 'title', 'tagnames', 'author_id', 'body', 'node_type',
    'parent_id', 'abs_parent_id', 'added_at', 'score', 'state_string',
    'last_edited_id', 'last_activity_by_id', 'last_activity_at',
    'active_revision_id', 'extra', 'extra_ref_id', 'extra_count', 'marked'
]

REVISION_COLS_MYSQL = [
    'id', 'node_id', 'title', 'tagnames', 'body', 'author_id',
    'revised_at', 'summary', 'revision'
]

def parse_sql_value(value_str):
    """Parse a single SQL value and return Python value"""
    value_str = value_str.strip()

    if value_str == 'NULL':
        return None
    elif value_str.startswith("'") and value_str.endswith("'"):
        # String value - remove quotes and unescape
        val = value_str[1:-1]
        val = val.replace("''", "'")  # SQLite escape
        val = val.replace('\\n', '\n')
        val = val.replace('\\r', '\r')
        val = val.replace('\\t', '\t')
        return val
    else:
        # Numeric or other
        try:
            if '.' in value_str:
                return float(value_str)
            else:
                return int(value_str)
        except:
            return value_str

def parse_values_row(values_str):
    """Parse a VALUES clause like (1,'text',NULL) into a list of values"""
    if values_str.startswith('(') and values_str.endswith(')'):
        values_str = values_str[1:-1]

    values = []
    current = ''
    in_string = False
    escape_next = False
    paren_depth = 0

    for char in values_str:
        if escape_next:
            current += char
            escape_next = False
            continue

        if char == '\\':
            current += char
            escape_next = True
            continue

        if char == "'" and not escape_next:
            in_string = not in_string
            current += char
            continue

        if in_string:
            current += char
            continue

        if char == '(':
            paren_depth += 1
            current += char
        elif char == ')':
            paren_depth -= 1
            current += char
        elif char == ',' and paren_depth == 0:
            values.append(parse_sql_value(current.strip()))
            current = ''
        else:
            current += char

    if current.strip():
        values.append(parse_sql_value(current.strip()))

    return values

node_data = []
revision_data = []

print("  → Reading SQL file...")
with open('importData/osqa-split.sql', encoding='utf-8', errors='ignore') as f:
    for line in f:
        line = line.strip()

        if line.startswith('INSERT INTO `forum_node` VALUES'):
            match = re.match(r"INSERT INTO `forum_node` VALUES (.+);", line)
            if match:
                try:
                    values = parse_values_row(match.group(1))
                    if len(values) == len(NODE_COLS_MYSQL):
                        node_dict = dict(zip(NODE_COLS_MYSQL, values))
                        node_data.append(node_dict)
                except Exception:
                    error_count += 1

        elif line.startswith('INSERT INTO `forum_noderevision` VALUES'):
            match = re.match(r"INSERT INTO `forum_noderevision` VALUES (.+);", line)
            if match:
                try:
                    values = parse_values_row(match.group(1))
                    if len(values) == len(REVISION_COLS_MYSQL):
                        rev_dict = dict(zip(REVISION_COLS_MYSQL, values))
                        revision_data.append(rev_dict)
                except Exception:
                    error_count += 1

print(f"  ✓ Parsed {len(node_data)} nodes and {len(revision_data)} revisions")

# Import nodes directly into SQLite with correct column order
print("  → Importing nodes...")
node_count = 0
for node_dict in node_data:
    try:
        # Insert with explicit column names
        sql = """INSERT INTO forum_node
                 (id, title, tagnames, body, node_type, added_at, score,
                  state_string, last_activity_at, extra, extra_count, marked,
                  active_revision_id, author_id, last_activity_by_id,
                  last_edited_id, abs_parent_id, extra_ref_id, parent_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        cursor.execute(sql, (
            node_dict['id'],
            node_dict['title'],
            node_dict['tagnames'],
            node_dict['body'],
            node_dict['node_type'],
            node_dict['added_at'],
            node_dict['score'],
            node_dict['state_string'],
            node_dict['last_activity_at'],
            node_dict['extra'],
            node_dict['extra_count'],
            node_dict['marked'],
            node_dict['active_revision_id'],
            node_dict['author_id'],
            node_dict['last_activity_by_id'],
            node_dict['last_edited_id'],
            node_dict['abs_parent_id'],
            node_dict['extra_ref_id'],
            node_dict['parent_id']
        ))
        node_count += 1

        if node_count % 100 == 0:
            conn.commit()
            print(f"    Progress: {node_count}/{len(node_data)} nodes...", end='\r')

    except Exception as e:
        if 'UNIQUE constraint' not in str(e):
            print(f"\n  ⚠ Error importing node {node_dict.get('id')}: {e}")
            error_count += 1

conn.commit()
print(f"\n  ✓ Imported {node_count} nodes")

# Import revisions
print("  → Importing revisions...")
revision_count = 0
for rev_dict in revision_data:
    try:
        sql = """INSERT INTO forum_noderevision
                 (id, title, tagnames, body, summary, revision, revised_at, node_id, author_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        cursor.execute(sql, (
            rev_dict['id'],
            rev_dict['title'],
            rev_dict['tagnames'],
            rev_dict['body'],
            rev_dict['summary'],
            rev_dict['revision'],
            rev_dict['revised_at'],
            rev_dict['node_id'],
            rev_dict['author_id']
        ))
        revision_count += 1

        if revision_count % 100 == 0:
            conn.commit()
            print(f"    Progress: {revision_count}/{len(revision_data)} revisions...", end='\r')

    except Exception as e:
        if 'UNIQUE constraint' not in str(e):
            print(f"\n  ⚠ Error importing revision {rev_dict.get('id')}: {e}")
            error_count += 1

conn.commit()
print(f"\n  ✓ Imported {revision_count} revisions")

# Re-enable foreign keys
cursor.execute("PRAGMA foreign_keys = ON")
conn.commit()
conn.close()

print("\n" + "="*80)
print("✓ DATA IMPORT COMPLETED")
print("="*80)
print("\nStatistics:")
print(f"  - Phase 1 (regular tables): {imported_count:,} rows")
print(f"  - Phase 2 (nodes): {node_count} rows")
print(f"  - Phase 2 (revisions): {revision_count} rows")
print(f"  - Skipped: {skipped_count:,} rows")
print(f"  - Errors: {error_count}")

# Verify
print("\nVerifying imported data...")
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
cursor.execute('SELECT node_type, COUNT(*) FROM forum_node GROUP BY node_type')
nodes = cursor.fetchall()
cursor.execute('SELECT COUNT(*) FROM auth_user')
users = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*) FROM forum_tag')
tags = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*) FROM forum_vote')
votes = cursor.fetchone()[0]
conn.close()

print("\nImported data:")
print(f"  - Users: {users:,}")
print(f"  - Tags: {tags}")
print(f"  - Votes: {votes:,}")
if nodes:
    print("  - Nodes by type:")
    for node_type, count in sorted(nodes):
        print(f"    - {node_type}: {count:,}")

# The data was written with raw SQL: drop cached objects and query results
# from any earlier database so the site doesn't serve stale rows.
from django.core.cache import cache
cache.clear()

print("\n✓ Original forum data imported successfully!")
print("\nYou can now run the development server:")
print("  ./venv/bin/python manage.py runserver")
print("\nHomepage: http://127.0.0.1:8000/")
print("="*80)
