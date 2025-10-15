#!/usr/bin/env python3
"""
Fix Python 2 to Python 3 syntax issues
"""
import os
import re

def fix_except_syntax(file_path):
    """Fix 'except Exception as e:' to 'except Exception as e:'"""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Fix except syntax
    original = content
    content = re.sub(r'except\s+(\w+(?:\.\w+)*)\s*,\s*(\w+)\s*:', r'except \1 as \2:', content)

    if content != original:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

def main():
    """Fix Python 2 syntax in all Python files"""
    fixed_count = 0
    for root, dirs, files in os.walk('.'):
        # Skip virtualenv and cache directories
        dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'venv', 'cache']]

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                if fix_except_syntax(file_path):
                    print(f"Fixed: {file_path}")
                    fixed_count += 1

    print(f"\n✓ Fixed {fixed_count} files")

if __name__ == '__main__':
    main()
