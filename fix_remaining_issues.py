#!/usr/bin/env python3
"""
Fix remaining Django 4.2 compatibility issues:
1. Remove context_instance parameter from render() calls
2. Fix render_to_string() with context_instance
3. Fix cmp() function usage
"""
import os
import re

def fix_context_instance(content):
    """Remove context_instance parameter from render() and render_to_string() calls"""

    # Pattern 1: render(..., context_instance=RequestContext(request))
    # Should become: render(...)
    content = re.sub(
        r',\s*context_instance=RequestContext\(request\)\)',
        ')',
        content
    )

    # Pattern 2: render_to_string(..., context_instance=RequestContext(request))
    # In Django 4.2, render_to_string takes request parameter
    # Change: render_to_string('template', context_instance=RequestContext(request))
    # To: render_to_string('template', request=request)
    content = re.sub(
        r'render_to_string\(([^,]+),\s*context_instance=RequestContext\(request\)\)',
        r'render_to_string(\1, request=request)',
        content
    )

    # Pattern 3: If there's a dict before context_instance
    # render_to_string('template', {}, context_instance=RequestContext(request))
    # To: render_to_string('template', {}, request=request)
    content = re.sub(
        r'render_to_string\(([^,]+),\s*(\{[^}]*\}),\s*context_instance=RequestContext\(request\)\)',
        r'render_to_string(\1, \2, request=request)',
        content
    )

    return content

def fix_cmp_function(content):
    """Fix cmp() function usage for Python 3"""

    # Pattern: sorted(..., cmp=...)
    # This is complex, so we'll look for specific patterns

    # Pattern 1: sorted(list, lambda a, b: cmp(a.field, b.field))
    # Replace with: sorted(list, key=lambda a: a.field)
    # Note: This assumes ascending order; if descending, we'll handle separately

    # For now, let's just document places where cmp is used
    # These will need manual review as the fix depends on context

    return content

def remove_requestcontext_import(content):
    """Remove RequestContext import if only used with context_instance"""
    # Check if RequestContext is imported but not used elsewhere
    # This is a simple cleanup - if it's imported and we removed all context_instance usage

    lines = content.split('\n')
    new_lines = []

    for line in lines:
        # If line imports RequestContext and nothing else important
        if 'from django.template import RequestContext' in line and 'import' in line:
            # Check if it imports other things too
            if ',' in line:
                # Multiple imports, just remove RequestContext
                line = line.replace(', RequestContext', '')
                line = line.replace('RequestContext, ', '')
            else:
                # Only RequestContext imported, skip this line
                continue
        new_lines.append(line)

    return '\n'.join(new_lines)

def process_file(filepath):
    """Process a single file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return False

    original = content

    # Apply fixes
    content = fix_context_instance(content)
    content = remove_requestcontext_import(content)
    # Note: cmp() fixes need manual review

    # Write back if changed
    if content != original:
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error writing {filepath}: {e}")
            return False
    return False

def main():
    """Main function"""
    base_dir = '/mnt/c/Users/sanch/git/osqa'

    # Files with context_instance issues
    files_to_fix = [
        'forum_modules/sximporter/views.py',
        'forum_modules/localauth/views.py',
        'forum/views/writers.py',
        'forum/views/users.py',
        'forum/views/readers.py',
        'forum/views/meta.py',
        'forum/views/auth.py',
        'forum/http_responses.py',
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
        else:
            print(f"File not found: {relpath}")

    print(f"\nTotal files fixed: {fixed_count}")

    print("\n=== Manual Review Needed ===")
    print("Files with cmp() function that need manual review:")
    print("  - forum_modules/akismet/startup.py")
    print("  - forum/views/meta.py")

if __name__ == '__main__':
    main()
