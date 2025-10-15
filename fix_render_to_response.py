#!/usr/bin/env python3
"""
Replace render_to_response with render (Django 1.3+, required in Django 3.0+)
"""
import os
import re

def fix_render_calls(file_path):
    """Replace render_to_response with render"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original = content
    changes = []

    # 1. Update import statement
    if 'from django.shortcuts import render' in content:
        content = content.replace(
            'from django.shortcuts import render',
            'from django.shortcuts import render'
        )
        changes.append('Updated import')

    # Also check for mixed imports
    if ', render' in content:
        content = content.replace(', render', ', render')
        changes.append('Updated mixed import')

    # 2. Replace render( with render(
    # But keep the function name in comments
    content = re.sub(
        r'\b(return\s+)?render_to_response\(',
        r'\1render(request, ',
        content
    )
    if 'render(request, ' in content and 'render_to_response' not in content:
        changes.append('Replaced function calls')

    # 3. Remove RequestContext where it appears
    # render(request, 'template', context)
    # becomes: render(request, 'template', context)

    # Pattern: render(request, 'template', context)
    content = re.sub(
        r'render\((.*?),\s*context_instance\s*=\s*RequestContext\([^)]+\)\)',
        r'render(\1)',
        content
    )

    # Pattern: render(request, 'template', context)
    # This is trickier - need to reorder parameters
    # For now, just remove the RequestContext part
    content = re.sub(
        r'render\(([^,]+),\s*([^,]+),\s*RequestContext\([^)]+\)\)',
        r'render(\1, \2)',
        content
    )

    # 4. Remove RequestContext import if present
    if 'from django.template import RequestContext' in content:
        # Check if RequestContext is still used elsewhere
        if 'RequestContext' not in content.replace('from django.template import RequestContext', ''):
            content = content.replace('from django.template import RequestContext\n', '')
            content = content.replace('from django.template import RequestContext', '')
            changes.append('Removed RequestContext import')

    # 5. Fix render() signature - first parameter should be request
    # render(request, 'template.html', {'context': data})
    # should be render(request, 'template.html', {'context': data})
    # This is complex, so we'll do a simple pattern match
    # Look for render( followed by a string (template name) as first parameter
    def add_request_param(match):
        # Check if request is already there
        params = match.group(1)
        if params.strip().startswith('request'):
            return match.group(0)  # Already has request
        # Add request as first parameter
        return f"render(request, {params}"

    # Only apply if render is used but request is not first param
    # This pattern looks for render( followed by a quote (indicating template is first)
    content = re.sub(
        r"render\((['\"])",
        r"render(request, \1",
        content
    )
    if 'render(request,' in content and changes:
        changes.append('Added request parameter')

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

    print("Fixing render_to_response calls...")

    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in [
            '.git', '__pycache__', 'venv', 'cache', 'osqa_env', 'tmp'
        ]]

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                changes = fix_render_calls(file_path)
                if changes:
                    fixed_count += 1
                    print(f"Fixed: {file_path} - {', '.join(changes)}")
                    for change in changes:
                        all_changes[change] = all_changes.get(change, 0) + 1

    print(f"\n✓ Fixed {fixed_count} files")
    if all_changes:
        print("\nChanges summary:")
        for change, count in all_changes.items():
            print(f"  {change}: {count}")

if __name__ == '__main__':
    main()
