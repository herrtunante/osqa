#!/usr/bin/env python3
"""
Add on_delete parameter to all ForeignKey fields for Django 2.0+ compatibility
"""
import os
import re

def fix_foreignkey(file_path):
    """Add on_delete=models.CASCADE to ForeignKey fields"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original = content
    modified = False

    # Pattern 1: ForeignKey without on_delete at end of line
    # models.ForeignKey(Model, on_delete=models.CASCADE) → models.ForeignKey(Model, on_delete=models.CASCADE)
    pattern1 = r'(models\.ForeignKey\([^)]+?)(\))'

    def add_on_delete(match):
        full_match = match.group(0)
        # Check if on_delete already exists
        if 'on_delete' in full_match:
            return full_match
        # Add on_delete before closing paren
        inner = match.group(1)
        return f"{inner}, on_delete=models.CASCADE)"

    new_content = re.sub(pattern1, add_on_delete, content)

    if new_content != content:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
        except Exception as e:
            print(f"Error writing {file_path}: {e}")
            return False

    return False

def main():
    count = 0

    print("Adding on_delete to ForeignKey fields...")

    for root, dirs, files in os.walk('.'):
        # Skip non-relevant directories
        dirs[:] = [d for d in dirs if d not in [
            '.git', '__pycache__', 'venv', 'cache', 'osqa_env', 'tmp'
        ]]

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                if fix_foreignkey(file_path):
                    print(f"Fixed: {file_path}")
                    count += 1

    print(f"\n✓ Modified {count} files")

if __name__ == '__main__':
    main()
