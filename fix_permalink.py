#!/usr/bin/env python3
"""
Fix @models.permalink decorator for Django 2.1+
"""
import re
import os

def fix_permalink_decorator(filepath):
    """Remove @models.permalink decorators"""
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # Simply remove the @models.permalink decorator line
    # The methods will need manual inspection, but removing the decorator
    # will at least let Django load
    content = re.sub(r'^\s*@models\.permalink\s*\n', '', content, flags=re.MULTILINE)

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False

def main():
    files = [
        '/mnt/c/Users/sanch/git/osqa/forum/models/meta.py',
        '/mnt/c/Users/sanch/git/osqa/forum/models/node.py',
        '/mnt/c/Users/sanch/git/osqa/forum/models/question.py',
        '/mnt/c/Users/sanch/git/osqa/forum/models/tag.py',
        '/mnt/c/Users/sanch/git/osqa/forum/models/user.py',
    ]

    fixed = 0
    for filepath in files:
        if os.path.exists(filepath):
            if fix_permalink_decorator(filepath):
                print(f"Fixed: {filepath}")
                fixed += 1

    print(f"\nTotal files fixed: {fixed}")
    print("\nNOTE: Methods using permalink need manual review to use reverse() directly")

if __name__ == '__main__':
    main()
