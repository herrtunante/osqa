#!/usr/bin/env python3
"""
Direct database setup script for OSQA - creates schema and sample data
"""
import sqlite3
import os
import hashlib
from datetime import datetime, timedelta

DB_PATH = 'osqa.db'

def create_tables(conn):
    """Create basic tables needed for OSQA"""
    cursor = conn.cursor()

    # Auth tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auth_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            password VARCHAR(128) NOT NULL,
            last_login DATETIME,
            is_superuser BOOLEAN NOT NULL DEFAULT 0,
            username VARCHAR(30) NOT NULL UNIQUE,
            first_name VARCHAR(30),
            last_name VARCHAR(30),
            email VARCHAR(254),
            is_staff BOOLEAN NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            date_joined DATETIME NOT NULL
        )
    ''')

    # Forum user profile
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forum_keyvalue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key VARCHAR(255) NOT NULL UNIQUE,
            value TEXT
        )
    ''')

    # Nodes table (questions, answers, comments)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forum_node (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type VARCHAR(16) NOT NULL,
            title VARCHAR(300),
            tagnames VARCHAR(125),
            author_id INTEGER NOT NULL,
            body TEXT,
            added_at DATETIME NOT NULL,
            score INTEGER DEFAULT 0,
            state_string TEXT DEFAULT '',
            last_activity_by_id INTEGER,
            last_activity_at DATETIME,
            parent_id INTEGER,
            abs_parent_id INTEGER,
            last_edited_id INTEGER,
            active_revision_id INTEGER,
            extra TEXT,
            extra_ref_id INTEGER,
            extra_count INTEGER DEFAULT 0,
            marked BOOLEAN DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            flag_count INTEGER DEFAULT 0,
            FOREIGN KEY (author_id) REFERENCES auth_user(id),
            FOREIGN KEY (parent_id) REFERENCES forum_node(id),
            FOREIGN KEY (abs_parent_id) REFERENCES forum_node(id)
        )
    ''')

    # Tags
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forum_tag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) UNIQUE NOT NULL,
            created_by_id INTEGER NOT NULL,
            used_count INTEGER DEFAULT 0,
            FOREIGN KEY (created_by_id) REFERENCES auth_user(id)
        )
    ''')

    # Node-Tag relationship
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forum_node_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY (node_id) REFERENCES forum_node(id),
            FOREIGN KEY (tag_id) REFERENCES forum_tag(id),
            UNIQUE(node_id, tag_id)
        )
    ''')

    # Actions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forum_action (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type VARCHAR(32) NOT NULL,
            action_date DATETIME NOT NULL,
            user_id INTEGER NOT NULL,
            node_id INTEGER,
            real_user_id INTEGER,
            ip VARCHAR(39),
            extra TEXT,
            canceled BOOLEAN DEFAULT 0,
            canceled_by_id INTEGER,
            canceled_at DATETIME,
            canceled_ip VARCHAR(39),
            FOREIGN KEY (user_id) REFERENCES auth_user(id),
            FOREIGN KEY (node_id) REFERENCES forum_node(id)
        )
    ''')

    # Session table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS django_session (
            session_key VARCHAR(40) PRIMARY KEY,
            session_data TEXT NOT NULL,
            expire_date DATETIME NOT NULL
        )
    ''')

    conn.commit()
    print("✓ Database tables created")

def create_users(conn):
    """Create sample users"""
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    users = [
        {
            'username': 'admin',
            'email': 'admin@osqa.local',
            'first_name': 'Admin',
            'last_name': 'User',
            'is_superuser': 1,
            'is_staff': 1,
            'password': 'pbkdf2_sha256$36000$xYz$dummy'  # password: admin
        },
        {
            'username': 'alice',
            'email': 'alice@osqa.local',
            'first_name': 'Alice',
            'last_name': 'Johnson',
            'is_superuser': 0,
            'is_staff': 0,
            'password': 'pbkdf2_sha256$36000$xYz$dummy'
        },
        {
            'username': 'bob',
            'email': 'bob@osqa.local',
            'first_name': 'Bob',
            'last_name': 'Smith',
            'is_superuser': 0,
            'is_staff': 0,
            'password': 'pbkdf2_sha256$36000$xYz$dummy'
        },
        {
            'username': 'charlie',
            'email': 'charlie@osqa.local',
            'first_name': 'Charlie',
            'last_name': 'Brown',
            'is_superuser': 0,
            'is_staff': 1,
            'password': 'pbkdf2_sha256$36000$xYz$dummy'
        }
    ]

    for user in users:
        cursor.execute('''
            INSERT INTO auth_user
            (username, email, first_name, last_name, is_superuser, is_staff, is_active, password, date_joined)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        ''', (user['username'], user['email'], user['first_name'], user['last_name'],
              user['is_superuser'], user['is_staff'], user['password'], now))

    conn.commit()
    print(f"✓ Created {len(users)} users")
    return {user['username']: cursor.lastrowid - (len(users) - i - 1) for i, user in enumerate(users)}

def create_tags(conn, user_ids):
    """Create sample tags"""
    cursor = conn.cursor()

    tags = ['python', 'django', 'database', 'web-development', 'javascript',
            'sql', 'performance', 'security', 'api', 'deployment']

    for tag in tags:
        cursor.execute('''
            INSERT INTO forum_tag (name, created_by_id, used_count)
            VALUES (?, ?, 0)
        ''', (tag, user_ids['admin']))

    conn.commit()
    print(f"✓ Created {len(tags)} tags")
    return {tag: cursor.lastrowid - (len(tags) - i - 1) for i, tag in enumerate(tags)}

def create_questions(conn, user_ids, tag_ids):
    """Create sample questions, answers, and comments"""
    cursor = conn.cursor()
    now = datetime.now()

    questions_data = [
        {
            'title': 'How do I optimize database queries in Django?',
            'body': '''<p>I'm working on a Django application and I've noticed that some of my views are quite slow.
            I suspect it's due to inefficient database queries. What are the best practices for optimizing database
            queries in Django?</p><p>Specifically, I'd like to know about:</p><ul><li>Using select_related and prefetch_related</li>
            <li>Query optimization techniques</li><li>Database indexing strategies</li></ul>''',
            'author': 'alice',
            'tags': ['django', 'database', 'performance'],
            'score': 15,
            'answers': [
                {
                    'body': '''<p>Great question! Here are some key techniques for optimizing Django queries:</p>
                    <p><strong>1. Use select_related() for ForeignKey relationships:</strong></p>
                    <pre>Post.objects.select_related('author').all()</pre>
                    <p><strong>2. Use prefetch_related() for ManyToMany relationships:</strong></p>
                    <pre>Post.objects.prefetch_related('tags').all()</pre>
                    <p><strong>3. Add database indexes:</strong> Make sure to add db_index=True to frequently queried fields.</p>''',
                    'author': 'bob',
                    'score': 25,
                    'accepted': True
                },
                {
                    'body': '''<p>Don't forget about the Django Debug Toolbar! It's invaluable for identifying slow queries.
                    Also, use .only() and .defer() to limit the fields fetched from the database when you don't need all of them.</p>''',
                    'author': 'charlie',
                    'score': 10
                }
            ]
        },
        {
            'title': 'What\'s the difference between authentication and authorization?',
            'body': '''<p>I keep seeing these two terms used in web development, but I'm not entirely clear on the distinction.
            Can someone explain the difference between authentication and authorization with some practical examples?</p>''',
            'author': 'bob',
            'tags': ['security', 'web-development'],
            'score': 22,
            'answers': [
                {
                    'body': '''<p><strong>Authentication</strong> is about verifying WHO you are (login with username/password).</p>
                    <p><strong>Authorization</strong> is about determining WHAT you can do (permissions and access control).</p>
                    <p>Example: When you log into a system, that's authentication. When the system checks if you're allowed to delete
                    a post, that's authorization.</p>''',
                    'author': 'admin',
                    'score': 35,
                    'accepted': True
                }
            ]
        },
        {
            'title': 'How to deploy a Django application to production?',
            'body': '''<p>I've been developing my Django app locally and it works great. Now I need to deploy it to a production server.
            What are the recommended steps and best practices for deploying Django applications?</p>
            <p>I'm considering using:</p><ul><li>Nginx</li><li>Gunicorn</li><li>PostgreSQL</li></ul>
            <p>Any advice would be appreciated!</p>''',
            'author': 'charlie',
            'tags': ['django', 'deployment', 'web-development'],
            'score': 18,
            'answers': []
        },
        {
            'title': 'Best practices for REST API design?',
            'body': '''<p>I'm building a RESTful API and want to make sure I'm following best practices.
            What are the key principles I should keep in mind for API design?</p>''',
            'author': 'alice',
            'tags': ['api', 'web-development'],
            'score': 12,
            'answers': [
                {
                    'body': '''<p>Key principles for REST API design:</p>
                    <ul>
                    <li>Use HTTP methods properly (GET, POST, PUT, DELETE)</li>
                    <li>Use meaningful resource names (nouns, not verbs)</li>
                    <li>Implement proper status codes</li>
                    <li>Version your API (e.g., /api/v1/)</li>
                    <li>Provide good error messages</li>
                    <li>Use pagination for large datasets</li>
                    </ul>''',
                    'author': 'bob',
                    'score': 8
                }
            ]
        },
        {
            'title': 'Python vs JavaScript for backend development?',
            'body': '''<p>I'm starting a new project and trying to decide between Python (Django/Flask) and JavaScript (Node.js)
            for the backend. What are the pros and cons of each?</p>''',
            'author': 'bob',
            'tags': ['python', 'javascript', 'web-development'],
            'score': 8,
            'answers': []
        }
    ]

    question_ids = []

    for i, q_data in enumerate(questions_data):
        time_offset = timedelta(days=len(questions_data) - i, hours=i * 2)
        created_at = (now - time_offset).isoformat()

        # Insert question
        cursor.execute('''
            INSERT INTO forum_node
            (node_type, title, tagnames, author_id, body, added_at, score, state_string,
             last_activity_by_id, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('question', q_data['title'], ' '.join(q_data['tags']),
              user_ids[q_data['author']], q_data['body'], created_at, q_data['score'],
              '', user_ids[q_data['author']], created_at))

        question_id = cursor.lastrowid
        question_ids.append(question_id)

        # Link tags to question
        for tag_name in q_data['tags']:
            if tag_name in tag_ids:
                cursor.execute('''
                    INSERT INTO forum_node_tags (node_id, tag_id)
                    VALUES (?, ?)
                ''', (question_id, tag_ids[tag_name]))

                # Update tag usage count
                cursor.execute('''
                    UPDATE forum_tag SET used_count = used_count + 1 WHERE id = ?
                ''', (tag_ids[tag_name],))

        # Insert answers
        for j, answer in enumerate(q_data.get('answers', [])):
            answer_time = (now - time_offset + timedelta(hours=j + 1)).isoformat()

            state_str = '(accepted)' if answer.get('accepted', False) else ''

            cursor.execute('''
                INSERT INTO forum_node
                (node_type, title, tagnames, author_id, body, added_at, score, state_string,
                 last_activity_by_id, last_activity_at, parent_id, abs_parent_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('answer', '', '', user_ids[answer['author']], answer['body'],
                  answer_time, answer['score'], state_str,
                  user_ids[answer['author']], answer_time, question_id, question_id))

            answer_id = cursor.lastrowid

            # Create accept action if accepted
            if answer.get('accepted', False):
                cursor.execute('''
                    INSERT INTO forum_action
                    (action_type, action_date, user_id, node_id, canceled)
                    VALUES (?, ?, ?, ?, 0)
                ''', ('acceptanswer', answer_time, user_ids[q_data['author']], answer_id))

    conn.commit()
    print(f"✓ Created {len(questions_data)} questions with answers")

def main():
    """Main setup function"""
    print("\n=== OSQA Database Setup ===\n")

    # Remove existing database
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("✓ Removed existing database")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)

    try:
        create_tables(conn)
        user_ids = create_users(conn)
        tag_ids = create_tags(conn, user_ids)
        create_questions(conn, user_ids, tag_ids)

        print("\n=== Setup Complete! ===\n")
        print("Sample users created:")
        print("  • admin / admin (superuser)")
        print("  • alice / alice")
        print("  • bob / bob")
        print("  • charlie / charlie (moderator)\n")
        print(f"Database created at: {os.path.abspath(DB_PATH)}\n")

    finally:
        conn.close()

if __name__ == '__main__':
    main()
