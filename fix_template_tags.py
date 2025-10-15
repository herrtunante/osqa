#!/usr/bin/env python3
"""
Fix template tags for Django 4.2 compatibility
"""
import os
import re

def fix_raise_syntax(content):
    """Fix Python 2 style raise statements"""
    # Pattern: raise Exception, "message"
    pattern = r'raise\s+([\w.]+),\s+(.*)'

    def replace_raise(match):
        exception = match.group(1)
        message = match.group(2)
        return f'raise {exception}({message})'

    return re.sub(pattern, replace_raise, content)

def fix_smart_unicode_imports(content):
    """Replace smart_unicode with smart_str, force_unicode with force_str"""
    # Fix imports
    content = content.replace(
        'from django.utils.encoding import smart_unicode, force_unicode, smart_str',
        'from django.utils.encoding import smart_str, force_str'
    )
    content = content.replace(
        'from django.utils.encoding import smart_unicode',
        'from django.utils.encoding import smart_str'
    )

    # Fix usage
    content = content.replace('smart_unicode(', 'smart_str(')
    content = content.replace('force_unicode(', 'force_str(')

    return content

def fix_unittest_asserts(content):
    """Fix deprecated unittest assert methods"""
    content = content.replace('self.assert_(', 'self.assertTrue(')
    return content

def fix_template_context(content):
    """
    Fix template.Context usage for Django 4.2
    In Django 4.2, direct Context() usage should be avoided in favor of dict context
    """
    # For template.render(), just pass the dict directly
    # Pattern: template.render(template.Context({...}))
    # Replace with: template.render({...})

    # This is a complex pattern, let's do it carefully
    pattern = r'\.render\(template\.Context\(([^)]+)\)\)'
    content = re.sub(pattern, r'.render(\1)', content)

    return content

def process_file(filepath):
    """Process a single file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # Apply fixes
    content = fix_raise_syntax(content)
    content = fix_smart_unicode_imports(content)
    content = fix_unittest_asserts(content)
    content = fix_template_context(content)

    # Write back if changed
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

def main():
    """Main function"""
    base_dir = '/mnt/c/Users/sanch/git/osqa'

    # Template tag files to fix
    files_to_fix = [
        'forum/templatetags/email_tags.py',
        'forum/templatetags/extra_tags.py',
        'forum/templatetags/node_tags.py',
        'forum/templatetags/user_tags.py',
        'forum/templatetags/smart_if.py',
        'forum/templatetags/question_list_tags.py',
    ]

    fixed_count = 0
    for relpath in files_to_fix:
        filepath = os.path.join(base_dir, relpath)
        if os.path.exists(filepath):
            if process_file(filepath):
                print(f"Fixed: {relpath}")
                fixed_count += 1
            else:
                print(f"No changes needed: {relpath}")

    print(f"\nTotal files fixed: {fixed_count}")

if __name__ == '__main__':
    main()
