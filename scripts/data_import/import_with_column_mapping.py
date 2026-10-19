#!/usr/bin/env python
"""
Import MySQL data with proper column mapping
The MySQL dump has columns in a different order than SQLite
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
print("OSQA Data Import with Column Mapping")
print("="*80)

# Column mapping: MySQL order -> SQLite column names
TABLE_MAPPINGS = {
    'forum_node': {
        'mysql_order': [
            'id', 'title', 'tagnames', 'author_id', 'body', 'node_type',
            'parent_id', 'abs_parent_id', 'added_at', 'score', 'state_string',
            'last_edited_id', 'last_activity_by_id', 'last_activity_at',
            'active_revision_id', 'extra', 'extra_ref_id', 'extra_count', 'marked'
        ],
        'sqlite_order': [
            'id', 'title', 'tagnames', 'body', 'node_type', 'added_at',
            'score', 'state_string', 'last_activity_at', 'extra', 'extra_count',
            'marked', 'active_revision_id', 'author_id', 'last_activity_by_id',
            'last_edited_id', 'abs_parent_id', 'extra_ref_id', 'parent_id'
        ]
    },
    'forum_noderevision': {
        'mysql_order': ['id', 'node_id', 'title', 'tagnames', 'body', 'author_id', 'revised_at', 'summary', 'revision'],
        'sqlite_order': ['id', 'title', 'tagnames', 'body', 'summary', 'revision', 'revised_at', 'node_id', 'author_id']
    }
}

# Connect to SQLite
print("\n[1/3] Connecting to SQLite database...")
conn = sqlite3.connect('osqa.db')
cursor = conn.cursor()
print("  ✓ Connected")

# Skip tables
skip_tables = {'django_session', 'south_migrationhistory', 'forum_mysqlftsindex'}

# Disable foreign key checks
cursor.execute("PRAGMA foreign_keys = OFF")

# Clear existing data
print("\n[2/3] Clearing existing data...")
try:
    cursor.execute("DELETE FROM forum_node_tags")
    cursor.execute("DELETE FROM forum_noderevision")
    cursor.execute("DELETE FROM forum_nodestate")
    cursor.execute("DELETE FROM forum_node")
    cursor.execute("DELETE FROM forum_action")
    cursor.execute("DELETE FROM forum_vote")
    conn.commit()
    print("  ✓ Cleared existing data")
except Exception as e:
    print(f"  ⚠ Error clearing data: {e}")
    conn.rollback()

# Parse and import
print("\n[3/3] Importing data with column mapping...")
imported_count = 0
skipped_count = 0
error_count = 0
current_table = None

def parse_values(values_str):
    """Parse VALUES clause into a list of values"""
    # Simple parser for VALUES (val1, val2, ...)
    # Remove outer parentheses
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
            values.append(current.strip())
            current = ''
        else:
            current += char

    if current.strip():
        values.append(current.strip())

    return values

with open('importData/osqa-split.sql', encoding='utf-8', errors='ignore') as f:
    line_num = 0
    batch = []
    batch_size = 500

    for line in f:
        line_num += 1
        line = line.strip()

        if not line or line.startswith('--') or line.startswith('/*') or line.startswith('/*!'):
            continue

        if line.startswith('INSERT INTO'):
            # Extract table name and values
            match = re.match(r"INSERT INTO `([^`]+)` VALUES (.+);", line)
            if not match:
                continue

            table = match.group(1)
            values_str = match.group(2)

            # Skip certain tables
            if table in skip_tables:
                skipped_count += 1
                continue

            # Show progress when switching tables
            if current_table != table:
                if current_table and batch:
                    # Commit previous batch
                    try:
                        cursor.executemany(batch[0], [b[1] for b in batch])
                        conn.commit()
                        imported_count += len(batch)
                    except Exception as e:
                        print(f"\n  ⚠ Batch error for {current_table}: {str(e)[:100]}")
                        error_count += len(batch)
                        conn.rollback()
                    batch = []

                current_table = table
                print(f"\n  → Importing {table}...")

            # Clean up MySQL escape sequences
            sql = line
            sql = sql.replace(r"\'", "''")
            sql = sql.replace('\\n', '\n')
            sql = sql.replace('\\r', '\r')
            sql = sql.replace('\\t', '\t')
            sql = sql.replace('\\"', '"')

            # For tables with column mapping, reorder the values
            if table in TABLE_MAPPINGS:
                try:
                    values = parse_values(values_str)
                    mysql_cols = TABLE_MAPPINGS[table]['mysql_order']
                    sqlite_cols = TABLE_MAPPINGS[table]['sqlite_order']

                    # Create mapping from MySQL position to value
                    value_map = dict(zip(mysql_cols, values))

                    # Reorder to SQLite column order
                    reordered = [value_map[col] for col in sqlite_cols]

                    # Create new INSERT statement with explicit columns
                    cols_str = ', '.join(sqlite_cols)
                    vals_str = ', '.join(reordered)
                    sql = f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str});"
                except Exception as e:
                    print(f"\n  ⚠ Error mapping columns for {table}: {str(e)[:100]}")
                    error_count += 1
                    continue

            # Add to batch
            if table in TABLE_MAPPINGS:
                # For mapped tables, we need to use executemany
                try:
                    cursor.execute(sql)
                    imported_count += 1
                    if imported_count % 100 == 0:
                        conn.commit()
                except Exception as e:
                    if 'UNIQUE constraint' not in str(e) and 'FOREIGN KEY constraint' not in str(e):
                        print(f"\n  ⚠ Error: {str(e)[:100]}")
                    error_count += 1
            else:
                # For other tables, batch them
                batch.append(sql)
                if len(batch) >= batch_size:
                    success = 0
                    for sql_stmt in batch:
                        try:
                            cursor.execute(sql_stmt)
                            success += 1
                        except Exception as e:
                            if 'UNIQUE constraint' not in str(e):
                                error_count += 1
                    conn.commit()
                    imported_count += success
                    batch = []

        if line_num % 10000 == 0:
            print(f"  Progress: {line_num:,} lines, {imported_count:,} imported, {error_count} errors...", end='\r')

# Final batch
if batch:
    for sql in batch:
        try:
            cursor.execute(sql)
            imported_count += 1
        except:
            error_count += 1
    conn.commit()

# Re-enable foreign keys
cursor.execute("PRAGMA foreign_keys = ON")
conn.commit()
conn.close()

print(f"\n  ✓ Processed {line_num:,} lines")

print("\n" + "="*80)
print("✓ DATA IMPORT COMPLETED")
print("="*80)
print("\nStatistics:")
print(f"  - Imported: {imported_count:,} rows")
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
    print("  - Nodes:")
    for node_type, count in sorted(nodes):
        print(f"    - {node_type}: {count:,}")

print("\n✓ Original forum data imported successfully!")
print("\nYou can now run the development server:")
print("  ./venv/bin/python manage.py runserver")
print("\nHomepage: http://127.0.0.1:8000/")
print("="*80)
