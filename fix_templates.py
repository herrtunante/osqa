#!/usr/bin/env python3
"""
Script to replace deprecated Django template tags:
- {% ifequal a b %} -> {% if a == b %}
- {% ifnotequal a b %} -> {% if a != b %}
- {% endifequal %} -> {% endif %}
- {% endifnotequal %} -> {% endif %}
"""

import re
import sys

files = [
    '/mnt/c/Users/sanch/git/osqa/forum_modules/exporter/templates/running.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/openforis/header.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/users/stats.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/users/signature.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/users/info.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/question_summary_list_roll.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/question_list/sort_tabs.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/question_list/item.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/question.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/page.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/osqaadmin/nodeman.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/osqaadmin/index.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/osqaadmin/dashboard.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/node/vote_buttons.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/node/post_controls.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/node/comments.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/header.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/base_content.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/auth/signin.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/auth/auth_settings.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/users/votes.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/users/tabs.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/revisions.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/paginator/sort_tabs.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/paginator/page_sizes.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/paginator/page_numbers.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/paginator.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/pagesize.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/notifications/newcomment.html',
    '/mnt/c/Users/sanch/git/osqa/forum/skins/default/templates/node/contributors_info.html',
]

def fix_template(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_content = content

        # Replace {% ifequal a b %} with {% if a == b %}
        content = re.sub(r'\{%\s*ifequal\s+(\S+)\s+(\S+)\s*%\}', r'{% if \1 == \2 %}', content)

        # Replace {% ifnotequal a b %} with {% if a != b %}
        content = re.sub(r'\{%\s*ifnotequal\s+(\S+)\s+(\S+)\s*%\}', r'{% if \1 != \2 %}', content)

        # Replace {% endifequal %} with {% endif %}
        content = re.sub(r'\{%\s*endifequal\s*%\}', r'{% endif %}', content)

        # Replace {% endifnotequal %} with {% endif %}
        content = re.sub(r'\{%\s*endifnotequal\s*%\}', r'{% endif %}', content)

        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"Fixed: {file_path}")
            return True
        else:
            print(f"No changes needed: {file_path}")
            return False
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

if __name__ == '__main__':
    fixed_count = 0
    for file_path in files:
        if fix_template(file_path):
            fixed_count += 1

    print(f"\nTotal files fixed: {fixed_count}/{len(files)}")
