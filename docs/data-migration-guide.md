> **For moving a live forum, follow [upgrading-from-original-osqa.md](upgrading-from-original-osqa.md).**
> This older document describes the database tables and alternative approaches in more detail.

# OSQA Database Migration Guide
## From Django 1.6 (Old Database) to Django 4.2 (New Version)

This guide will help you migrate data from your old OSQA database (Django 1.6) to the new Django 4.2 version.

---

## Table of Contents
1. [Database Overview](#database-overview)
2. [Core Tables Structure](#core-tables-structure)
3. [Migration Methods](#migration-methods)
4. [Step-by-Step Migration](#step-by-step-migration)
5. [Validation & Testing](#validation--testing)

---

## Database Overview

### Current Database (Django 4.2)
- **Type:** SQLite (development)
- **File:** `osqa.db`
- **Location:** `/mnt/c/Users/sanch/git/osqa/`
- **Size:** ~752KB (empty, only schema)

### Total Tables: 65 tables
- **Django System Tables:** 9 (auth, admin, sessions, etc.)
- **OSQA Core Tables:** 56 (users, questions, answers, comments, votes, etc.)

---

## Core Tables Structure

### 1. User Management

#### `auth_user` - Django User Authentication
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `username` (varchar 150) - Unique username
  - `email` (varchar 254) - User email
  - `password` (varchar 128) - Hashed password
  - `first_name`, `last_name` (varchar 150)
  - `is_staff`, `is_active`, `is_superuser` (bool)
  - `date_joined`, `last_login` (datetime)

#### `forum_user` - OSQA User Profile Extension
- **Primary Key:** `user_ptr_id` (links to auth_user.id)
- **Key Fields:**
  - `reputation` (INTEGER) - User reputation score
  - `gold`, `silver`, `bronze` (integer unsigned) - Badge counts
  - `is_approved`, `email_isvalid` (bool)
  - `real_name`, `website`, `location` (text fields)
  - `about` (TEXT) - User bio
  - `last_seen` (datetime)

### 2. Content (Questions, Answers, Comments)

#### `forum_node` - Polymorphic Content Table
- **Primary Key:** `id` (INTEGER)
- **Type Field:** `node_type` (varchar 16)
  - Values: `question`, `answer`, `comment`
- **Key Fields:**
  - `title` (varchar 300) - Question/Answer title
  - `body` (TEXT) - Content body (markdown)
  - `tagnames` (varchar 125) - Space-separated tag names
  - `author_id` (INTEGER) - Foreign key to auth_user
  - `parent_id` (bigint) - Parent node (for answers/comments)
  - `added_at` (datetime) - Creation timestamp
  - `score` (INTEGER) - Vote score
  - `state_string` (TEXT) - Node state (published, deleted, etc.)
  - `active_revision_id` (bigint) - Current revision
  - `extra_count` (INTEGER) - Comment/answer count
  - `marked` (bool) - Favorited/flagged

#### `forum_node_tags` - Many-to-Many: Nodes ↔ Tags
- `id` (INTEGER)
- `node_id` (bigint) - Foreign key to forum_node
- `tag_id` (bigint) - Foreign key to forum_tag

#### `forum_noderevision` - Content Edit History
- `id` (INTEGER)
- `revision` (integer unsigned) - Revision number
- `summary` (varchar 300) - Edit summary
- `body` (TEXT) - Content at this revision
- `revised_at` (datetime)
- `author_id`, `node_id` (foreign keys)

### 3. Tags

#### `forum_tag`
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `name` (varchar 255) - Tag name (unique)
  - `created_at` (datetime)
  - `used_count` (integer unsigned) - Number of questions
  - `created_by_id` (INTEGER) - Foreign key to auth_user

### 4. Actions & Reputation

#### `forum_action` - Base Action Table
- **Primary Key:** `id` (INTEGER)
- **Type Field:** `action_type` (varchar 32)
- **Key Fields:**
  - `user_id` (INTEGER) - User performing action
  - `node_id` (bigint) - Target node
  - `action_date` (datetime)
  - `ip` (varchar 39) - IP address
  - `canceled` (bool) - Action canceled?
  - `extra` (TEXT) - JSON extra data

**Inherited Action Tables** (all inherit from forum_action):
- `forum_askaction` - Ask question
- `forum_answeraction` - Post answer
- `forum_commentaction` - Post comment
- `forum_voteupaction`, `forum_votedownaction` - Voting
- `forum_acceptansweraction` - Accept answer
- `forum_editprofileaction` - Profile edits
- `forum_awardaction` - Badge awards
- `forum_flagaction` - Flag content
- `forum_deleteaction` - Delete content
- And 20+ more action types...

#### `forum_actionrepute` - Reputation Changes
- Links actions to reputation changes
- Tracks reputation gained/lost per action

### 5. Voting

#### `forum_vote`
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `user_id` (INTEGER) - Voter
  - `node_id` (bigint) - Voted content
  - `action_id` (bigint) - Related action
  - `value` (smallint) - Vote value (+1 or -1)
  - `voted_at` (datetime)

### 6. Badges & Awards

#### `forum_badge`
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `cls` (varchar 50) - Badge class name
  - `type` (smallint) - 1=Gold, 2=Silver, 3=Bronze
  - `awarded_count` (integer unsigned)

#### `forum_award`
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `user_id` (INTEGER) - Award recipient
  - `badge_id` (bigint) - Badge type
  - `node_id` (bigint) - Content that triggered award
  - `awarded_at` (datetime)
  - `action_id` (bigint) - Related action

### 7. Settings & Configuration

#### `forum_keyvalue` - Runtime Settings
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `key` (varchar 255) - Setting name
  - `value` (TEXT) - Setting value (pickled Python object)

### 8. Subscriptions

#### `forum_questionsubscription`
- **Primary Key:** `id` (INTEGER)
- **Key Fields:**
  - `user_id` (INTEGER) - Subscriber
  - `question_id` (bigint) - Question being followed
  - `auto_subscription` (bool)
  - `last_view` (datetime)

---

## Migration Methods

### Method 1: Direct Database Migration (MySQL/PostgreSQL)
**Best for:** Large databases with lots of content

### Method 2: Data Export/Import via Django
**Best for:** Small-medium databases or cross-database migrations

### Method 3: Manual SQL Migration
**Best for:** Complex scenarios or selective migration

---

## Step-by-Step Migration

### Prerequisites

1. **Backup your old database:**
   ```bash
   # For MySQL
   mysqldump -u username -p osqa_old > osqa_backup.sql

   # For PostgreSQL
   pg_dump -U username osqa_old > osqa_backup.sql
   ```

2. **Verify old database access:**
   - Note database host, username, password, database name
   - Test connection to old database

---

## METHOD 1: Direct Database Migration (MySQL → MySQL/PostgreSQL)

### Step 1: Configure New Database in Django 4.2

Edit `settings_local.py`:

```python
# Option A: MySQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'osqa_new',
        'USER': 'your_mysql_user',
        'PASSWORD': 'your_mysql_password',
        'HOST': 'localhost',
        'PORT': '3306',
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

# Option B: PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'osqa_new',
        'USER': 'your_pg_user',
        'PASSWORD': 'your_pg_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### Step 2: Install Database Driver

```bash
# For MySQL
./venv/bin/pip install mysqlclient

# For PostgreSQL
./venv/bin/pip install psycopg2-binary
```

### Step 3: Create New Database

```bash
# MySQL
mysql -u root -p
CREATE DATABASE osqa_new CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON osqa_new.* TO 'osqa_user'@'localhost';

# PostgreSQL
sudo -u postgres psql
CREATE DATABASE osqa_new ENCODING 'UTF8';
CREATE USER osqa_user WITH PASSWORD 'password';
GRANT ALL PRIVILEGES ON DATABASE osqa_new TO osqa_user;
```

### Step 4: Run Migrations on New Database

```bash
./venv/bin/python manage.py migrate
```

### Step 5: Dump Old Database Data (Skip Schema)

```bash
# MySQL - Data only (no CREATE TABLE statements)
mysqldump -u username -p --no-create-info --skip-add-drop-table \
  --complete-insert osqa_old > osqa_data.sql

# PostgreSQL
pg_dump -U username --data-only --column-inserts osqa_old > osqa_data.sql
```

### Step 6: Clean Up Dump File

The old database has South migrations and some Django 1.6 tables we don't need:

```bash
# Remove South migration data
sed -i '/south_migrationhistory/d' osqa_data.sql

# Remove Django 1.6 specific tables if present
sed -i '/django_content_type.*contenttypes/d' osqa_data.sql
```

### Step 7: Import Data to New Database

**IMPORTANT:** You may need to:
1. Disable foreign key checks temporarily
2. Update sequence numbers after import

```sql
-- MySQL
SET FOREIGN_KEY_CHECKS=0;
SOURCE osqa_data.sql;
SET FOREIGN_KEY_CHECKS=1;

-- PostgreSQL
BEGIN;
SET CONSTRAINTS ALL DEFERRED;
\i osqa_data.sql
COMMIT;
```

### Step 8: Update Sequences (PostgreSQL only)

```sql
-- Reset all sequence values to max(id) + 1
SELECT setval('auth_user_id_seq', (SELECT MAX(id) FROM auth_user));
SELECT setval('forum_node_id_seq', (SELECT MAX(id) FROM forum_node));
SELECT setval('forum_tag_id_seq', (SELECT MAX(id) FROM forum_tag));
-- Repeat for all tables with auto-increment IDs
```

---

## METHOD 2: Data Export/Import via Django

This method uses Django's built-in serialization to export/import data.

### Step 1: Export from Old Database

On your **OLD Django 1.6 installation**:

```bash
# Export all data to JSON
python manage.py dumpdata --natural-foreign --natural-primary \
  --exclude auth.permission --exclude contenttypes \
  --exclude sessions --exclude admin.logentry \
  > osqa_data.json
```

### Step 2: Review and Clean JSON

The exported file may need adjustments:

```python
# clean_json.py
import json

with open('osqa_data.json', 'r') as f:
    data = json.load(f)

# Remove South migration entries
data = [item for item in data if item['model'] != 'south.migrationhistory']

# Save cleaned data
with open('osqa_data_clean.json', 'w') as f:
    json.dump(data, f, indent=2)
```

### Step 3: Import to New Database

On your **NEW Django 4.2 installation**:

```bash
# Clear existing data (if any)
./venv/bin/python manage.py flush

# Load the data
./venv/bin/python manage.py loaddata osqa_data_clean.json
```

---

## METHOD 3: Custom Migration Script

For complex scenarios, write a Python script:

```python
# migrate_data.py
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.contrib.auth.models import User
from forum.models import Node, Tag, Vote, Badge, Award
import MySQLdb  # or psycopg2

# Connect to OLD database
old_db = MySQLdb.connect(
    host='localhost',
    user='old_user',
    passwd='old_password',
    db='osqa_old'
)
cursor = old_db.cursor(MySQLdb.cursors.DictCursor)

# Migrate Users
cursor.execute("SELECT * FROM auth_user")
for row in cursor.fetchall():
    user, created = User.objects.get_or_create(
        id=row['id'],
        defaults={
            'username': row['username'],
            'email': row['email'],
            'password': row['password'],  # Already hashed
            'is_staff': row['is_staff'],
            'is_active': row['is_active'],
            'date_joined': row['date_joined'],
        }
    )
    if created:
        print(f"Migrated user: {user.username}")

# Migrate Nodes (Questions, Answers, Comments)
cursor.execute("SELECT * FROM forum_node ORDER BY id")
for row in cursor.fetchall():
    node, created = Node.objects.get_or_create(
        id=row['id'],
        defaults={
            'title': row['title'],
            'body': row['body'],
            'node_type': row['node_type'],
            'author_id': row['author_id'],
            'added_at': row['added_at'],
            'score': row['score'],
            # ... other fields
        }
    )
    if created:
        print(f"Migrated node: {node.id} ({node.node_type})")

# Continue for other tables...
# Tags, Votes, Badges, Awards, Actions, etc.

old_db.close()
print("Migration complete!")
```

Run the script:
```bash
./venv/bin/python migrate_data.py
```

---

## Validation & Testing

### 1. Verify Record Counts

```sql
-- Compare counts between old and new databases
SELECT 'Users' as table_name, COUNT(*) as count FROM auth_user
UNION ALL
SELECT 'Questions', COUNT(*) FROM forum_node WHERE node_type='question'
UNION ALL
SELECT 'Answers', COUNT(*) FROM forum_node WHERE node_type='answer'
UNION ALL
SELECT 'Comments', COUNT(*) FROM forum_node WHERE node_type='comment'
UNION ALL
SELECT 'Tags', COUNT(*) FROM forum_tag
UNION ALL
SELECT 'Votes', COUNT(*) FROM forum_vote
UNION ALL
SELECT 'Badges', COUNT(*) FROM forum_badge
UNION ALL
SELECT 'Awards', COUNT(*) FROM forum_award;
```

### 2. Check Data Integrity

```python
# verify_migration.py
from django.contrib.auth.models import User
from forum.models import Node, Tag

# Check user integrity
total_users = User.objects.count()
users_with_profiles = User.objects.filter(forum_user__isnull=False).count()
print(f"Total users: {total_users}")
print(f"Users with profiles: {users_with_profiles}")

# Check questions have authors
orphan_questions = Node.objects.filter(
    node_type='question',
    author__isnull=True
).count()
print(f"Orphaned questions: {orphan_questions}")

# Check answers have parents
orphan_answers = Node.objects.filter(
    node_type='answer',
    parent__isnull=True
).count()
print(f"Orphaned answers: {orphan_answers}")

# Check tag usage
tags_with_zero_count = Tag.objects.filter(used_count=0).count()
print(f"Tags with zero usage: {tags_with_zero_count}")
```

### 3. Test Application Features

1. **Browse questions:** http://localhost:8000/questions/
2. **View user profiles:** http://localhost:8000/users/
3. **Check tags:** http://localhost:8000/tags/
4. **Test search functionality**
5. **Verify badges:** http://localhost:8000/badges/
6. **Check admin interface:** http://localhost:8000/nimda/

### 4. Compare with Old Database

Access both databases simultaneously:

```python
# compare_databases.py
DATABASES = {
    'default': {  # New Django 4.2 database
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'osqa_new',
        ...
    },
    'old': {  # Old Django 1.6 database
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'osqa_old',
        ...
    }
}

from django.db import connections

# Compare user counts
cursor_new = connections['default'].cursor()
cursor_old = connections['old'].cursor()

cursor_new.execute("SELECT COUNT(*) FROM auth_user")
count_new = cursor_new.fetchone()[0]

cursor_old.execute("SELECT COUNT(*) FROM auth_user")
count_old = cursor_old.fetchone()[0]

print(f"Old DB users: {count_old}")
print(f"New DB users: {count_new}")
print(f"Difference: {count_old - count_new}")
```

---

## Common Issues & Solutions

### Issue 1: Foreign Key Constraint Errors

**Problem:** Import fails with foreign key violations

**Solution:**
```sql
-- Temporarily disable constraints
SET FOREIGN_KEY_CHECKS=0;  -- MySQL
SET CONSTRAINTS ALL DEFERRED;  -- PostgreSQL
```

### Issue 2: Character Encoding Issues

**Problem:** Special characters display as �

**Solution:**
```python
# Ensure database uses UTF-8
# MySQL
'OPTIONS': {'charset': 'utf8mb4'}

# PostgreSQL - set encoding when creating database
CREATE DATABASE osqa_new ENCODING 'UTF8';
```

### Issue 3: Auto-increment Sequences Out of Sync

**Problem:** "Duplicate entry" errors when creating new records

**Solution (PostgreSQL):**
```sql
-- Reset all sequences
SELECT setval('auth_user_id_seq', COALESCE((SELECT MAX(id)+1 FROM auth_user), 1), false);
```

**Solution (MySQL):**
```sql
-- MySQL auto-fixes this, but you can force:
ALTER TABLE auth_user AUTO_INCREMENT = 1;
SET @num := 0;
UPDATE auth_user SET id = @num := (@num+1);
ALTER TABLE auth_user AUTO_INCREMENT =1;
```

### Issue 4: Missing Content Types

**Problem:** ContentType object has no attribute...

**Solution:**
```bash
# Regenerate content types
./venv/bin/python manage.py migrate --run-syncdb
./venv/bin/python manage.py remove_stale_contenttypes --noinput
```

### Issue 5: Password Hashes Not Working

**Problem:** Users can't log in after migration

**Solution:**
Password hashes should transfer directly. If there are issues:
```python
from django.contrib.auth.models import User

# Check password hash format
user = User.objects.get(username='testuser')
print(user.password)
# Should look like: pbkdf2_sha256$... or similar

# If needed, reset passwords
user.set_password('newpassword')
user.save()
```

---

## Post-Migration Tasks

### 1. Update File Paths

Check these settings in `settings_local.py`:
- `MEDIA_ROOT` - User uploaded files location
- `STATIC_ROOT` - Static files location

### 2. Migrate Uploaded Files

```bash
# Copy user uploads from old installation
cp -r /path/to/old/osqa/upfiles/* /path/to/new/osqa/upfiles/
```

### 3. Update Site Settings

Access admin or run:
```python
from forum.models import KeyValue

# Update site URL
KeyValue.objects.update_or_create(
    key='APP_URL',
    defaults={'value': 'http://yournewdomain.com'}
)
```

### 4. Rebuild Search Index

If using full-text search:
```bash
./venv/bin/python manage.py rebuild_index
```

### 5. Clear Caches

```bash
# If using file cache
rm -rf cache/*

# If using memcached
echo "flush_all" | nc localhost 11211
```

---

## Rollback Plan

Always have a rollback strategy:

1. **Keep old database intact** - Don't delete until migration is verified
2. **Backup new database** before importing:
   ```bash
   mysqldump -u user -p osqa_new > osqa_new_backup.sql
   ```
3. **Document all changes** made during migration
4. **Test rollback procedure**:
   ```bash
   # Drop and recreate database
   mysql -u root -p
   DROP DATABASE osqa_new;
   CREATE DATABASE osqa_new;

   # Restore from backup
   mysql -u user -p osqa_new < osqa_new_backup.sql
   ```

---

## Migration Checklist

- [ ] Backup old database
- [ ] Install new database (if switching)
- [ ] Configure `settings_local.py` with new database
- [ ] Run migrations on new database
- [ ] Export data from old database
- [ ] Clean exported data (remove South, etc.)
- [ ] Import data to new database
- [ ] Verify record counts match
- [ ] Test application functionality
- [ ] Migrate uploaded files
- [ ] Update site settings
- [ ] Test user logins
- [ ] Test content creation (ask question, post answer)
- [ ] Verify badges and reputation
- [ ] Check email functionality
- [ ] Performance test
- [ ] Document any issues
- [ ] Create new backup of migrated database

---

## Getting Help

If you encounter issues during migration:

1. **Check Django 4.2 migration guide:**
   https://docs.djangoproject.com/en/4.2/topics/migrations/

2. **OSQA documentation:**
   http://meta.osqa.net

3. **Database-specific documentation:**
   - MySQL: https://dev.mysql.com/doc/
   - PostgreSQL: https://www.postgresql.org/docs/

4. **Review migration logs:**
   ```bash
   ./venv/bin/python manage.py showmigrations
   ./venv/bin/python manage.py migrate --verbosity=3
   ```

---

## Summary

This guide covered three migration approaches:

1. **Direct Database Migration** - Fastest for same database type
2. **Django Fixtures (JSON)** - Safest cross-database migration
3. **Custom Script** - Most flexible for complex scenarios

Choose the method that best fits your:
- Database type (MySQL, PostgreSQL, SQLite)
- Data volume
- Technical expertise
- Migration timeline

Good luck with your migration!
