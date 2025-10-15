#!/usr/bin/env python3
"""
Fix deprecated Django imports for Django 4.2
"""
import os
import re

# Mapping of old imports to new imports
IMPORT_REPLACEMENTS = {
    # URL resolvers
    'from django.urls import': 'from django.urls import',
    'import django.urls': 'import django.urls',

    # Translation
    'from django.utils.translation import gettext_lazy': 'from django.utils.translation import gettext_lazy',
    'from django.utils.translation import gettext': 'from django.utils.translation import gettext',
    'from django.utils.translation import gettext_noop': 'from django.utils.translation import gettext_noop',
    'from django.utils.translation import ngettext': 'from django.utils.translation import ngettext',
    'from django.utils.translation import ngettext_lazy': 'from django.utils.translation import ngettext_lazy',

    # Encoding (removed in Django 4.0)
    'from django.utils.encoding import force_str': 'from django.utils.encoding import force_str',
    'from django.utils.encoding import force_bytes': 'from django.utils.encoding import force_bytes',
    'from django.utils.encoding import smart_str': 'from django.utils.encoding import smart_str',

    # Forms
    'from django.forms.utils import': 'from django.forms.utils import',

    # Tests (removed in Django 1.9)
    'from django.test.runner import': 'from django.test.runner import',

    # Admin (removed in Django 1.7)
    'from django.contrib.admin.utils import': 'from django.contrib.admin.utils import',
}

# Function/method replacements
FUNCTION_REPLACEMENTS = {
    # Translation aliases
    r'\bugettext\(': 'gettext(',
    r'\bugettext_lazy\(': 'gettext_lazy(',
    r'\bugettext_noop\(': 'gettext_noop(',
    r'\bungettext\(': 'ngettext(',
    r'\bungettext_lazy\(': 'ngettext_lazy(',

    # Encoding
    r'\bforce_text\(': 'force_str(',
    r'\bsmart_text\(': 'smart_str(',

    # is_authenticated / is_anonymous - changed from method to property in Django 1.10
    # These are handled carefully to not break things
    # We'll handle these separately
}

def fix_imports(file_path):
    """Fix deprecated imports and function calls"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original = content
    changes = []

    # Fix imports
    for old_import, new_import in IMPORT_REPLACEMENTS.items():
        if old_import in content:
            content = content.replace(old_import, new_import)
            changes.append(f"{old_import.split()[-1]} → {new_import.split()[-1]}")

    # Fix function calls
    for old_func, new_func in FUNCTION_REPLACEMENTS.items():
        new_content = re.sub(old_func, new_func, content)
        if new_content != content:
            content = new_content
            changes.append(f"{old_func} → {new_func}")

    # Fix is_authenticated() -> is_authenticated (property not method)
    # But be careful - only in user context
    if '.is_authenticated()' in content:
        content = re.sub(
            r'(user|request\.user)\.is_authenticated\(\)',
            r'\1.is_authenticated',
            content
        )
        changes.append('is_authenticated() → is_authenticated')

    if '.is_anonymous()' in content:
        content = re.sub(
            r'(user|request\.user)\.is_anonymous\(\)',
            r'\1.is_anonymous',
            content
        )
        changes.append('is_anonymous() → is_anonymous')

    # Fix __nonzero__ -> __bool__ (Python 3)
    if 'def __nonzero__' in content:
        content = content.replace('def __bool__(', 'def __bool__(')
        changes.append('__nonzero__ → __bool__')

    if content != original:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return changes
        except Exception as e:
            print(f"Error writing {file_path}: {e}")
            return False

    return False

def main():
    fixed_count = 0
    all_changes = {}

    print("Fixing deprecated imports and function calls...")

    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in [
            '.git', '__pycache__', 'venv', 'cache', 'osqa_env', 'tmp'
        ]]

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                changes = fix_imports(file_path)
                if changes:
                    fixed_count += 1
                    print(f"Fixed: {file_path}")
                    for change in changes:
                        all_changes[change] = all_changes.get(change, 0) + 1

    print(f"\n✓ Fixed {fixed_count} files")
    if all_changes:
        print("\nChanges summary:")
        for change, count in sorted(all_changes.items(), key=lambda x: -x[1]):
            print(f"  {change}: {count}")

if __name__ == '__main__':
    main()
