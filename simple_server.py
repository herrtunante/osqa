#!/usr/bin/env python3
"""
Simple HTTP server to browse OSQA data
"""
import sqlite3
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import html

DB_PATH = 'osqa.db'

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

class OSQAHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/':
            self.show_questions_list()
        elif path.startswith('/question/'):
            question_id = int(path.split('/')[-1])
            self.show_question(question_id)
        elif path == '/users':
            self.show_users()
        elif path == '/tags':
            self.show_tags()
        else:
            self.send_error(404)

    def show_questions_list(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = dict_factory
        cursor = conn.cursor()

        cursor.execute('''
            SELECT n.id, n.title, n.body, n.score, n.added_at, n.tagnames,
                   u.username as author,
                   (SELECT COUNT(*) FROM forum_node WHERE parent_id = n.id AND node_type = 'answer') as answer_count
            FROM forum_node n
            JOIN auth_user u ON n.author_id = u.id
            WHERE n.node_type = 'question'
            ORDER BY n.added_at DESC
        ''')

        questions = cursor.fetchall()

        html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <title>OSQA - Questions</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .header {{ background: #3498db; color: white; padding: 20px; margin: -20px -20px 20px -20px; }}
        .header h1 {{ margin: 0; }}
        .nav {{ margin: 15px 0; }}
        .nav a {{ color: white; text-decoration: none; margin-right: 20px; font-weight: bold; }}
        .nav a:hover {{ text-decoration: underline; }}
        .question {{ background: white; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .question-title {{ font-size: 18px; font-weight: bold; margin-bottom: 10px; }}
        .question-title a {{ color: #3498db; text-decoration: none; }}
        .question-title a:hover {{ text-decoration: underline; }}
        .question-meta {{ color: #666; font-size: 12px; }}
        .question-stats {{ float: right; text-align: center; }}
        .stat {{ display: inline-block; margin-left: 15px; padding: 5px 10px; background: #ecf0f1; border-radius: 3px; }}
        .stat-number {{ font-weight: bold; color: #3498db; }}
        .tags {{ margin-top: 10px; }}
        .tag {{ display: inline-block; background: #e8f4f8; color: #2c3e50; padding: 3px 8px; margin-right: 5px; border-radius: 3px; font-size: 12px; }}
        .excerpt {{ color: #555; margin-top: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>OSQA Forum</h1>
        <div class="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
        </div>
    </div>
    <h2>{len(questions)} Questions</h2>
'''

        for q in questions:
            # Extract excerpt from body (strip HTML tags)
            excerpt = html.escape(q['body'][:200]) + '...' if len(q['body']) > 200 else html.escape(q['body'])
            excerpt = excerpt.replace('<p>', '').replace('</p>', '').replace('<ul>', '').replace('</ul>', '')

            html_content += f'''
    <div class="question">
        <div class="question-stats">
            <span class="stat"><span class="stat-number">{q['score']}</span> votes</span>
            <span class="stat"><span class="stat-number">{q['answer_count']}</span> answers</span>
        </div>
        <div class="question-title">
            <a href="/question/{q['id']}">{html.escape(q['title'])}</a>
        </div>
        <div class="excerpt">{excerpt[:150]}...</div>
        <div class="tags">
            {' '.join(f'<span class="tag">{tag}</span>' for tag in q['tagnames'].split())}
        </div>
        <div class="question-meta">
            asked by <strong>{html.escape(q['author'])}</strong> on {q['added_at'][:10]}
        </div>
    </div>
'''

        html_content += '''
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_content.encode())
        conn.close()

    def show_question(self, question_id):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = dict_factory
        cursor = conn.cursor()

        cursor.execute('''
            SELECT n.id, n.title, n.body, n.score, n.added_at, n.tagnames,
                   u.username as author, u.first_name, u.last_name
            FROM forum_node n
            JOIN auth_user u ON n.author_id = u.id
            WHERE n.id = ? AND n.node_type = 'question'
        ''', (question_id,))

        question = cursor.fetchone()

        if not question:
            self.send_error(404)
            return

        cursor.execute('''
            SELECT n.id, n.body, n.score, n.added_at, n.state_string,
                   u.username as author, u.first_name, u.last_name
            FROM forum_node n
            JOIN auth_user u ON n.author_id = u.id
            WHERE n.parent_id = ? AND n.node_type = 'answer'
            ORDER BY n.score DESC, n.added_at ASC
        ''', (question_id,))

        answers = cursor.fetchall()

        html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <title>{html.escape(question['title'])} - OSQA</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .header {{ background: #3498db; color: white; padding: 20px; margin: -20px -20px 20px -20px; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .nav {{ margin: 15px 0; }}
        .nav a {{ color: white; text-decoration: none; margin-right: 20px; font-weight: bold; }}
        .question, .answer {{ background: white; padding: 20px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .question-title {{ font-size: 24px; margin-bottom: 15px; }}
        .vote {{ float: left; text-align: center; margin-right: 20px; }}
        .vote-count {{ font-size: 24px; font-weight: bold; color: #3498db; }}
        .content {{ overflow: hidden; }}
        .post-meta {{ color: #666; font-size: 13px; margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee; }}
        .tags {{ margin: 15px 0; }}
        .tag {{ display: inline-block; background: #e8f4f8; color: #2c3e50; padding: 3px 8px; margin-right: 5px; border-radius: 3px; font-size: 12px; }}
        .answers-header {{ font-size: 20px; margin: 30px 0 15px 0; }}
        .accepted {{ border-left: 3px solid #27ae60; }}
        .accepted-badge {{ background: #27ae60; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>OSQA Forum</h1>
        <div class="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
        </div>
    </div>

    <div class="question">
        <h1 class="question-title">{html.escape(question['title'])}</h1>
        <div class="vote">
            <div class="vote-count">{question['score']}</div>
            <div>votes</div>
        </div>
        <div class="content">
            <div>{question['body']}</div>
            <div class="tags">
                {' '.join(f'<span class="tag">{tag}</span>' for tag in question['tagnames'].split())}
            </div>
            <div class="post-meta">
                asked by <strong>{html.escape(question['first_name'])} {html.escape(question['last_name'])}</strong>
                ({html.escape(question['author'])}) on {question['added_at'][:10]}
            </div>
        </div>
    </div>

    <div class="answers-header">
        {len(answers)} Answer{'s' if len(answers) != 1 else ''}
    </div>
'''

        for answer in answers:
            is_accepted = '(accepted)' in answer['state_string']
            accepted_class = ' accepted' if is_accepted else ''
            accepted_badge = '<span class="accepted-badge">✓ ACCEPTED</span>' if is_accepted else ''

            html_content += f'''
    <div class="answer{accepted_class}">
        <div class="vote">
            <div class="vote-count">{answer['score']}</div>
            <div>votes</div>
        </div>
        <div class="content">
            {accepted_badge}
            <div>{answer['body']}</div>
            <div class="post-meta">
                answered by <strong>{html.escape(answer['first_name'])} {html.escape(answer['last_name'])}</strong>
                ({html.escape(answer['author'])}) on {answer['added_at'][:10]}
            </div>
        </div>
    </div>
'''

        html_content += '''
    <p><a href="/">&larr; Back to questions</a></p>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_content.encode())
        conn.close()

    def show_users(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = dict_factory
        cursor = conn.cursor()

        cursor.execute('''
            SELECT u.id, u.username, u.first_name, u.last_name, u.email, u.is_staff, u.is_superuser,
                   (SELECT COUNT(*) FROM forum_node WHERE author_id = u.id AND node_type = 'question') as questions,
                   (SELECT COUNT(*) FROM forum_node WHERE author_id = u.id AND node_type = 'answer') as answers
            FROM auth_user u
            ORDER BY questions DESC, answers DESC
        ''')

        users = cursor.fetchall()

        html_content = '''
<!DOCTYPE html>
<html>
<head>
    <title>Users - OSQA</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        .header { background: #3498db; color: white; padding: 20px; margin: -20px -20px 20px -20px; }
        .header h1 { margin: 0; }
        .nav { margin: 15px 0; }
        .nav a { color: white; text-decoration: none; margin-right: 20px; font-weight: bold; }
        .user { background: white; padding: 15px; margin-bottom: 10px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .user-name { font-size: 18px; font-weight: bold; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 10px; }
        .superuser { background: #e74c3c; color: white; }
        .staff { background: #f39c12; color: white; }
        .stats { color: #666; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>OSQA Forum</h1>
        <div class="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
        </div>
    </div>
    <h2>Users</h2>
'''

        for user in users:
            badges = ''
            if user['is_superuser']:
                badges += '<span class="badge superuser">ADMIN</span>'
            if user['is_staff']:
                badges += '<span class="badge staff">MODERATOR</span>'

            html_content += f'''
    <div class="user">
        <div class="user-name">
            {html.escape(user['first_name'])} {html.escape(user['last_name'])}
            ({html.escape(user['username'])})
            {badges}
        </div>
        <div class="stats">
            {user['questions']} questions | {user['answers']} answers
        </div>
    </div>
'''

        html_content += '''
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_content.encode())
        conn.close()

    def show_tags(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = dict_factory
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, name, used_count
            FROM forum_tag
            ORDER BY used_count DESC, name ASC
        ''')

        tags = cursor.fetchall()

        html_content = '''
<!DOCTYPE html>
<html>
<head>
    <title>Tags - OSQA</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        .header { background: #3498db; color: white; padding: 20px; margin: -20px -20px 20px -20px; }
        .header h1 { margin: 0; }
        .nav { margin: 15px 0; }
        .nav a { color: white; text-decoration: none; margin-right: 20px; font-weight: bold; }
        .tag-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }
        .tag-box { background: white; padding: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .tag-name { font-size: 16px; font-weight: bold; color: #3498db; }
        .tag-count { color: #666; font-size: 13px; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>OSQA Forum</h1>
        <div class="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
        </div>
    </div>
    <h2>Tags</h2>
    <div class="tag-grid">
'''

        for tag in tags:
            html_content += f'''
        <div class="tag-box">
            <div class="tag-name">{html.escape(tag['name'])}</div>
            <div class="tag-count">{tag['used_count']} questions</div>
        </div>
'''

        html_content += '''
    </div>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_content.encode())
        conn.close()

def run_server(port=8000):
    server_address = ('', port)
    httpd = HTTPServer(server_address, OSQAHandler)
    print(f"\n{'='*50}")
    print(f"  OSQA Forum Server Running!")
    print(f"{'='*50}")
    print(f"\n  📍 Server URL: http://localhost:{port}")
    print(f"  📍 Network URL: http://127.0.0.1:{port}")
    print(f"\n  Sample users (username / password):")
    print(f"    • admin / admin (superuser)")
    print(f"    • alice / alice")
    print(f"    • bob / bob")
    print(f"    • charlie / charlie (moderator)")
    print(f"\n  Press Ctrl+C to stop the server")
    print(f"{'='*50}\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")

if __name__ == '__main__':
    run_server()
