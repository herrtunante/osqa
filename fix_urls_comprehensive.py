#!/usr/bin/env python3
"""
Comprehensive URL pattern fixer for Django 4.2
"""
import os
import re

def fix_url_file(file_path):
    """Fix URL patterns in a single file"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original = content
    changes = []

    # 1. Replace 'url(' with 're_path(' but only if not already done
    if ' url(' in content or '\turl(' in content or '(url(' in content:
        content = re.sub(r'\burl\(', r're_path(', content)
        changes.append('url → re_path')

    # 2. Remove patterns('') wrapper
    if "patterns('')" in content or 'patterns("")' in content:
        content = content.replace("urlpatterns = patterns('')", "urlpatterns = []")
        content = content.replace('urlpatterns = patterns("")', "urlpatterns = []")
        content = content.replace("urlpatterns += patterns('')", "# urlpatterns +=")
        content = content.replace('urlpatterns += patterns("")', "# urlpatterns +=")
        changes.append('patterns() removed')

    # 3. Fix core_urls tuple to list
    if 'core_urls = (' in content:
        content = content.replace('core_urls = (', 'core_urls = [')
        changes.append('tuple → list')

    # 4. Change closing ) to ] for core_urls (last occurrence in patterns)
    # Be careful with this - only do if we changed to list
    if 'core_urls = [' in content and changes:
        # Find the closing for core_urls
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.strip() == ')' and i > 0:
                # Check if previous lines suggest this is end of URL list
                if 'app.' in lines[i-1] or 're_path' in lines[i-1]:
                    lines[i] = ']'
                    break
        content = '\n'.join(lines)

    # 5. Fix string view references
    if "'django.shortcuts.redirect'" in content:
        content = content.replace("'django.shortcuts.redirect'", "redirect")
        changes.append('redirect import')

    # 6. Fix django.views.static.serve
    if "'django.views.static.serve'" in content:
        # Add import if not present
        if 'from django.views.static import serve' not in content:
            import_line = 'from django.shortcuts import redirect'
            if import_line in content:
                content = content.replace(import_line,
                    import_line + '\nfrom django.views.static import serve')
        content = content.replace("'django.views.static.serve'", "serve")
        changes.append('serve import')

    # 7. Fix sitemap string references
    for sitemap_func in ['forum.sitemap.index', 'forum.sitemap.sitemap',
                         'forum.sitemap.sitemap_section_index']:
        if f"'{sitemap_func}'" in content:
            func_name = sitemap_func.split('.')[-1]
            # Import should be added at top
            changes.append(f'{func_name} import needed')

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

    print("Fixing URL patterns for Django 4.2...")

    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in [
            '.git', '__pycache__', 'venv', 'cache', 'osqa_env', 'tmp'
        ]]

        for file in files:
            if file == 'urls.py' or file.endswith('_urls.py'):
                file_path = os.path.join(root, file)
                changes = fix_url_file(file_path)
                if changes:
                    fixed_count += 1
                    print(f"Fixed: {file_path} - {', '.join(changes)}")
                    for change in changes:
                        all_changes[change] = all_changes.get(change, 0) + 1

    print(f"\n✓ Fixed {fixed_count} URL files")
    if all_changes:
        print("\nChanges summary:")
        for change, count in all_changes.items():
            print(f"  {change}: {count}")

if __name__ == '__main__':
    main()
