#!/usr/bin/env python3
"""
Fix all remaining middleware files to Django 4.2 style
"""
import os
import re

middleware_files = {
    'forum/middleware/django_cookies.py': {
        'add_init': True,
        'add_call': True,
    },
    'forum/middleware/cancel.py': {
        'add_init': True,
        'add_call': True,
    },
    'forum/middleware/request_utils.py': {
        'add_init': True,
        'add_call': True,
    },
    'forum/middleware/admin_messages.py': {
        'add_init': True,
        'add_call': True,
    },
    'forum/middleware/custom_pages.py': {
        'add_init': True,
        'add_call': True,
    },
}

def fix_middleware(file_path):
    """Add new-style middleware methods"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    # Find class definition
    class_line_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('class ') and '(object)' in line or '(Object)' in line or line.strip().endswith(':'):
            class_line_idx = i
            break

    if class_line_idx is None:
        print(f"No class found in {file_path}")
        return False

    # Check if already has __init__ with get_response
    has_new_init = any('def __init__(self, get_response)' in line for line in lines)
    has_call = any('def __call__(self, request)' in line for line in lines)

    if has_new_init and has_call:
        print(f"Skipping {file_path} - already converted")
        return False

    # Find indentation
    indent = '    '

    # Find where to insert (after class definition)
    insert_idx = class_line_idx + 1

    new_lines = []

    if not has_new_init:
        new_lines.append(f'{indent}def __init__(self, get_response):\n')
        new_lines.append(f'{indent}    self.get_response = get_response\n')
        new_lines.append('\n')

    if not has_call:
        new_lines.append(f'{indent}def __call__(self, request):\n')
        new_lines.append(f'{indent}    response = None\n')
        new_lines.append(f'{indent}    if hasattr(self, "process_request"):\n')
        new_lines.append(f'{indent}        response = self.process_request(request)\n')
        new_lines.append(f'{indent}    if response is None:\n')
        new_lines.append(f'{indent}        response = self.get_response(request)\n')
        new_lines.append(f'{indent}    if hasattr(self, "process_response"):\n')
        new_lines.append(f'{indent}        response = self.process_response(request, response)\n')
        new_lines.append(f'{indent}    return response\n')
        new_lines.append('\n')

    if new_lines:
        # Insert after class definition
        lines = lines[:insert_idx] + new_lines + lines[insert_idx:]

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return True
        except Exception as e:
            print(f"Error writing {file_path}: {e}")
            return False

    return False

def main():
    fixed_count = 0

    print("Fixing remaining middleware files...")

    for file_path in middleware_files.keys():
        if os.path.exists(file_path):
            if fix_middleware(file_path):
                print(f"✓ Fixed: {file_path}")
                fixed_count += 1
        else:
            print(f"✗ Not found: {file_path}")

    print(f"\n✓ Fixed {fixed_count} middleware files")

if __name__ == '__main__':
    main()
