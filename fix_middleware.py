#!/usr/bin/env python3
"""
Convert old-style Django middleware to new-style (Django 1.10+)
"""
import os
import re

def convert_middleware(file_path):
    """Convert middleware class to new style"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original = content
    changes = []

    # Check if this is old-style middleware
    if 'def process_request(self, request):' in content:
        # Already has process_request, check if it needs __call__
        if 'def __call__(self, request):' not in content:
            # This is old-style middleware, needs conversion
            # For now, add a simple __call__ wrapper

            # Find the class definition
            class_match = re.search(r'class\s+(\w+).*?:', content)
            if class_match:
                # Add __call__ method after __init__ if it exists
                if 'def __init__' in content:
                    # Add after __init__
                    init_pattern = r'(def __init__\(self.*?\):.*?(?:\n.*?)*?(?=\n    def |$))'

                    def add_call_method(match):
                        init_code = match.group(0)
                        call_method = '''

    def __call__(self, request):
        # New-style middleware entry point
        response = None
        if hasattr(self, 'process_request'):
            response = self.process_request(request)
        if response is None:
            response = self.get_response(request)
        if hasattr(self, 'process_response'):
            response = self.process_response(request, response)
        return response
'''
                        return init_code + call_method

                    new_content = re.sub(init_pattern, add_call_method, content, count=1)
                    if new_content != content:
                        content = new_content
                        changes.append('Added __call__ method')

                # Update __init__ to store get_response
                if 'def __init__(self):' in content:
                    content = content.replace(
                        'def __init__(self):',
                        'def __init__(self, get_response):\n        self.get_response = get_response'
                    )
                    changes.append('Updated __init__ signature')
                elif 'def __init__(self, ' in content and 'get_response' not in content:
                    # Add get_response parameter
                    content = re.sub(
                        r'def __init__\(self,',
                        r'def __init__(self, get_response,',
                        content,
                        count=1
                    )
                    # Add self.get_response assignment
                    content = re.sub(
                        r'(def __init__\(self, get_response.*?\):)',
                        r'\1\n        self.get_response = get_response',
                        content,
                        count=1
                    )
                    changes.append('Updated __init__ with get_response')

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

    print("Converting middleware to new-style Django 4.2...")

    for root, dirs, files in os.walk('./forum/middleware'):
        for file in files:
            if file.endswith('.py') and file != '__init__.py':
                file_path = os.path.join(root, file)
                changes = convert_middleware(file_path)
                if changes:
                    fixed_count += 1
                    print(f"Fixed: {file_path} - {', '.join(changes)}")
                    for change in changes:
                        all_changes[change] = all_changes.get(change, 0) + 1

    print(f"\n✓ Fixed {fixed_count} middleware files")
    if all_changes:
        print("\nChanges summary:")
        for change, count in all_changes.items():
            print(f"  {change}: {count}")

if __name__ == '__main__':
    main()
