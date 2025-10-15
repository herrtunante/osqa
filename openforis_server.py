#!/usr/bin/env python3
"""
OpenForis-styled OSQA server
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

class OpenForisHandler(BaseHTTPRequestHandler):
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
    <title>Open Foris OSQA - Questions</title>
    <meta charset="UTF-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; }}

        /* OpenForis Header Styling */
        #roof {{ background: #2c5f2d; color: white; padding: 15px 0; border-bottom: 3px solid #97be0d; }}
        #logo {{ text-align: center; padding: 10px 0; }}
        #logo a {{ text-decoration: none; color: white; font-size: 32px; font-weight: bold; }}

        #nav {{ background: #97be0d; padding: 10px 0; text-align: center; }}
        #nav a {{ color: white; text-decoration: none; padding: 10px 20px; margin: 0 5px; display: inline-block; font-weight: bold; border-radius: 3px; }}
        #nav a:hover, #nav a.on {{ background: #7ca00b; }}
        #nav a.special {{ background: #2c5f2d; }}
        #nav a.special:hover {{ background: #234d24; }}

        #searchBar {{ background: white; padding: 15px; text-align: center; border-bottom: 1px solid #ddd; }}
        .gsc-control-cse {{ border: none !important; background: transparent !important; }}

        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}

        .question {{ background: white; padding: 20px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 3px solid #97be0d; position: relative; }}
        .question-title {{ font-size: 18px; font-weight: bold; margin-bottom: 10px; padding-right: 180px; }}
        .question-title a {{ color: #2c5f2d; text-decoration: none; }}
        .question-title a:hover {{ color: #97be0d; }}
        .question-meta {{ color: #666; font-size: 12px; margin-top: 10px; }}
        .question-stats {{ position: absolute; right: 20px; top: 20px; text-align: center; }}
        .stat {{ display: inline-block; margin-left: 15px; padding: 8px 15px; background: #f0f0f0; border-radius: 3px; text-align: center; min-width: 60px; }}
        .stat-number {{ font-weight: bold; color: #2c5f2d; font-size: 18px; display: block; }}
        .stat-label {{ font-size: 11px; color: #666; display: block; margin-top: 3px; }}
        .tags {{ margin-top: 10px; clear: both; }}
        .tag {{ display: inline-block; background: #e8f4f8; color: #2c5f2d; padding: 4px 10px; margin-right: 5px; margin-top: 5px; border-radius: 3px; font-size: 12px; border: 1px solid #97be0d; }}
        .excerpt {{ color: #555; margin-top: 10px; line-height: 1.6; }}

        .page-header {{ color: #2c5f2d; font-size: 24px; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #97be0d; }}

        .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; margin-top: 40px; }}
    </style>
    <script async src="https://cse.google.com/cse.js?cx=011544878288102132035:auatq0p6-wo"></script>
</head>
<body>
    <div id="roof">
        <div id="logo">
            <a href="/">Open Foris OSQA</a>
        </div>
        <div id="nav">
            <a href="/" class="on">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
            <a href="/" class="special">Ask a Question</a>
        </div>
    </div>

    <div id="searchBar">
        <gcse:search></gcse:search>
    </div>

    <div class="container">
        <h2 class="page-header">{len(questions)} Questions</h2>
'''

        for q in questions:
            # Extract excerpt from body
            excerpt = html.escape(q['body'][:200]) + '...' if len(q['body']) > 200 else html.escape(q['body'])
            excerpt = excerpt.replace('&lt;p&gt;', '').replace('&lt;/p&gt;', '').replace('&lt;ul&gt;', '').replace('&lt;/ul&gt;', '')

            html_content += f'''
    <div class="question">
        <div class="question-stats">
            <span class="stat">
                <span class="stat-number">{q['score']}</span>
                <span class="stat-label">votes</span>
            </span>
            <span class="stat">
                <span class="stat-number">{q['answer_count']}</span>
                <span class="stat-label">answers</span>
            </span>
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
        <div class="footer">
            Powered by Open Foris OSQA - Open Source Q&amp;A Platform
        </div>
    </div>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
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
    <title>{html.escape(question['title'])} - Open Foris OSQA</title>
    <meta charset="UTF-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; }}

        #roof {{ background: #2c5f2d; color: white; padding: 15px 0; border-bottom: 3px solid #97be0d; }}
        #logo {{ text-align: center; padding: 10px 0; }}
        #logo a {{ text-decoration: none; color: white; font-size: 32px; font-weight: bold; }}

        #nav {{ background: #97be0d; padding: 10px 0; text-align: center; }}
        #nav a {{ color: white; text-decoration: none; padding: 10px 20px; margin: 0 5px; display: inline-block; font-weight: bold; border-radius: 3px; }}
        #nav a:hover, #nav a.on {{ background: #7ca00b; }}
        #nav a.special {{ background: #2c5f2d; }}
        #nav a.special:hover {{ background: #234d24; }}

        #searchBar {{ background: white; padding: 15px; text-align: center; border-bottom: 1px solid #ddd; }}

        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}

        .question, .answer {{ background: white; padding: 20px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .question {{ border-left: 3px solid #97be0d; }}
        .question-title {{ font-size: 28px; margin-bottom: 15px; color: #2c5f2d; }}
        .vote {{ float: left; text-align: center; margin-right: 20px; min-width: 60px; }}
        .vote-count {{ font-size: 32px; font-weight: bold; color: #2c5f2d; }}
        .vote-label {{ font-size: 12px; color: #666; }}
        .content {{ overflow: hidden; line-height: 1.6; }}
        .post-meta {{ color: #666; font-size: 13px; margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee; }}
        .tags {{ margin: 15px 0; }}
        .tag {{ display: inline-block; background: #e8f4f8; color: #2c5f2d; padding: 4px 10px; margin-right: 5px; border-radius: 3px; font-size: 12px; border: 1px solid #97be0d; }}
        .answers-header {{ font-size: 24px; margin: 30px 0 15px 0; color: #2c5f2d; padding-bottom: 10px; border-bottom: 2px solid #97be0d; }}
        .accepted {{ border-left: 3px solid #27ae60; background: #f0fff4; }}
        .accepted-badge {{ background: #27ae60; color: white; padding: 5px 12px; border-radius: 3px; font-size: 12px; margin-bottom: 10px; display: inline-block; }}
        .back-link {{ display: inline-block; margin: 20px 0; padding: 10px 20px; background: #97be0d; color: white; text-decoration: none; border-radius: 3px; }}
        .back-link:hover {{ background: #7ca00b; }}
    </style>
    <script async src="https://cse.google.com/cse.js?cx=011544878288102132035:auatq0p6-wo"></script>
</head>
<body>
    <div id="roof">
        <div id="logo">
            <a href="/">Open Foris OSQA</a>
        </div>
        <div id="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users">Users</a>
            <a href="/" class="special">Ask a Question</a>
        </div>
    </div>

    <div id="searchBar">
        <gcse:search></gcse:search>
    </div>

    <div class="container">
        <div class="question">
            <h1 class="question-title">{html.escape(question['title'])}</h1>
            <div class="vote">
                <div class="vote-count">{question['score']}</div>
                <div class="vote-label">votes</div>
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
            <div class="vote-label">votes</div>
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
        <a href="/" class="back-link">&larr; Back to questions</a>
    </div>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
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

        html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Users - Open Foris OSQA</title>
    <meta charset="UTF-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; }}

        #roof {{ background: #2c5f2d; color: white; padding: 15px 0; border-bottom: 3px solid #97be0d; }}
        #logo {{ text-align: center; padding: 10px 0; }}
        #logo a {{ text-decoration: none; color: white; font-size: 32px; font-weight: bold; }}

        #nav {{ background: #97be0d; padding: 10px 0; text-align: center; }}
        #nav a {{ color: white; text-decoration: none; padding: 10px 20px; margin: 0 5px; display: inline-block; font-weight: bold; border-radius: 3px; }}
        #nav a:hover, #nav a.on {{ background: #7ca00b; }}
        #nav a.special {{ background: #2c5f2d; }}
        #nav a.special:hover {{ background: #234d24; }}

        #searchBar {{ background: white; padding: 15px; text-align: center; border-bottom: 1px solid #ddd; }}

        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}

        .page-header {{ color: #2c5f2d; font-size: 24px; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #97be0d; }}

        .user {{ background: white; padding: 15px; margin-bottom: 10px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 3px solid #97be0d; }}
        .user-name {{ font-size: 18px; font-weight: bold; color: #2c5f2d; }}
        .badge {{ display: inline-block; padding: 3px 10px; border-radius: 3px; font-size: 11px; margin-left: 10px; font-weight: bold; }}
        .superuser {{ background: #e74c3c; color: white; }}
        .staff {{ background: #f39c12; color: white; }}
        .stats {{ color: #666; margin-top: 5px; }}
    </style>
    <script async src="https://cse.google.com/cse.js?cx=011544878288102132035:auatq0p6-wo"></script>
</head>
<body>
    <div id="roof">
        <div id="logo">
            <a href="/">Open Foris OSQA</a>
        </div>
        <div id="nav">
            <a href="/">Questions</a>
            <a href="/tags">Tags</a>
            <a href="/users" class="on">Users</a>
            <a href="/" class="special">Ask a Question</a>
        </div>
    </div>

    <div id="searchBar">
        <gcse:search></gcse:search>
    </div>

    <div class="container">
        <h2 class="page-header">{len(users)} Users</h2>
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
    </div>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
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

        html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Tags - Open Foris OSQA</title>
    <meta charset="UTF-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; }}

        #roof {{ background: #2c5f2d; color: white; padding: 15px 0; border-bottom: 3px solid #97be0d; }}
        #logo {{ text-align: center; padding: 10px 0; }}
        #logo a {{ text-decoration: none; color: white; font-size: 32px; font-weight: bold; }}

        #nav {{ background: #97be0d; padding: 10px 0; text-align: center; }}
        #nav a {{ color: white; text-decoration: none; padding: 10px 20px; margin: 0 5px; display: inline-block; font-weight: bold; border-radius: 3px; }}
        #nav a:hover, #nav a.on {{ background: #7ca00b; }}
        #nav a.special {{ background: #2c5f2d; }}
        #nav a.special:hover {{ background: #234d24; }}

        #searchBar {{ background: white; padding: 15px; text-align: center; border-bottom: 1px solid #ddd; }}

        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}

        .page-header {{ color: #2c5f2d; font-size: 24px; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #97be0d; }}

        .tag-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }}
        .tag-box {{ background: white; padding: 15px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 3px solid #97be0d; }}
        .tag-name {{ font-size: 16px; font-weight: bold; color: #2c5f2d; }}
        .tag-count {{ color: #666; font-size: 13px; margin-top: 5px; }}
    </style>
    <script async src="https://cse.google.com/cse.js?cx=011544878288102132035:auatq0p6-wo"></script>
</head>
<body>
    <div id="roof">
        <div id="logo">
            <a href="/">Open Foris OSQA</a>
        </div>
        <div id="nav">
            <a href="/">Questions</a>
            <a href="/tags" class="on">Tags</a>
            <a href="/users">Users</a>
            <a href="/" class="special">Ask a Question</a>
        </div>
    </div>

    <div id="searchBar">
        <gcse:search></gcse:search>
    </div>

    <div class="container">
        <h2 class="page-header">{len(tags)} Tags</h2>
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
    </div>
</body>
</html>
'''

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
        conn.close()

def run_server(port=8000):
    server_address = ('', port)
    httpd = HTTPServer(server_address, OpenForisHandler)
    print(f"\n{'='*60}")
    print(f"  Open Foris OSQA Server Running!")
    print(f"{'='*60}")
    print(f"\n  🌲 Server URL: http://localhost:{port}")
    print(f"  🌲 Network URL: http://127.0.0.1:{port}")
    print(f"\n  Features:")
    print(f"    • OpenForis branding and styling")
    print(f"    • Google Custom Search integration")
    print(f"    • Questions, Answers, Tags, and Users")
    print(f"\n  Press Ctrl+C to stop the server")
    print(f"{'='*60}\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")

if __name__ == '__main__':
    run_server()
