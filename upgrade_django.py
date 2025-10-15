#!/usr/bin/env python3
"""
Comprehensive Django 1.6 → 4.2 upgrade script
"""
import os
import re
import sys

class DjangoUpgrader:
    def __init__(self):
        self.stats = {
            'files_processed': 0,
            'files_modified': 0,
            'changes': {}
        }

    def log_change(self, change_type):
        self.stats['changes'][change_type] = self.stats['changes'].get(change_type, 0) + 1

    def process_file(self, file_path):
        """Process a single Python file"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return False

        self.stats['files_processed'] += 1
        original = content

        # Apply all fixes
        content = self.fix_python3_syntax(content, file_path)
        content = self.fix_django_imports(content, file_path)
        content = self.fix_django_patterns(content, file_path)

        # Write if changed
        if content != original:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.stats['files_modified'] += 1
                return True
            except Exception as e:
                print(f"Error writing {file_path}: {e}")

        return False

    def fix_python3_syntax(self, content, file_path):
        """Fix Python 2 to Python 3 syntax"""
        # 1. except Exception as e: → except Exception as e:
        new_content = re.sub(r'except\s+(\w+(?:\.\w+)*)\s*,\s*(\w+)\s*:', r'except \1 as \2:', content)
        if new_content != content:
            self.log_change('except syntax')
            content = new_content

        # 2. __unicode__ → __str__
        new_content = re.sub(r'def __unicode__\(', r'def __str__(', content)
        if new_content != content:
            self.log_change('__unicode__ → __str__')
            content = new_content

        # 3. iteritems() → items()
        new_content = re.sub(r'\.iteritems\(\)', r'.items()', content)
        if new_content != content:
            self.log_change('iteritems()')
            content = new_content

        # 4. iterkeys() → keys()
        new_content = re.sub(r'\.iterkeys\(\)', r'.keys()', content)
        if new_content != content:
            self.log_change('iterkeys()')
            content = new_content

        # 5. itervalues() → values()
        new_content = re.sub(r'\.itervalues\(\)', r'.values()', content)
        if new_content != content:
            self.log_change('itervalues()')
            content = new_content

        # 6. range → range
        new_content = re.sub(r'\bxrange\b', r'range', content)
        if new_content != content:
            self.log_change('range')
            content = new_content

        # 7. str() → str()
        new_content = re.sub(r'\bunicode\(', r'str(', content)
        if new_content != content:
            self.log_change('str()')
            content = new_content

        # 8. str → str
        new_content = re.sub(r'\bbasestring\b', r'str', content)
        if new_content != content:
            self.log_change('str')
            content = new_content

        return content

    def fix_django_imports(self, content, file_path):
        """Fix Django imports for 4.2"""
        # django.conf.urls.defaults → django.urls
        if 'from django.urls import' in content:
            content = content.replace('from django.urls import', 'from django.urls import')
            self.log_change('django.conf.urls.defaults')

        # django.conf.urls → django.urls for url, include
        if 'from django.conf.urls import patterns' in content:
            content = re.sub(
                r'from django\.conf\.urls import patterns, url',
                r'from django.urls import re_path as url',
                content
            )
            self.log_change('patterns import removed')

        if 'from django.urls import re_path as url' in content:
            content = content.replace(
                'from django.urls import re_path as url',
                'from django.urls import re_path as url'
            )
            self.log_change('url → re_path')

        # render_to_response → render
        if 'from django.shortcuts import render' in content:
            content = content.replace(
                'from django.shortcuts import render',
                'from django.shortcuts import render'
            )
            self.log_change('render_to_response import')

        # Update context processors paths
        content = content.replace(
            'django.template.context_processors.',
            'django.template.context_processors.'
        )

        # Update contrib.auth context processors
        content = content.replace(
            'django.contrib.auth.context_processors.auth',
            'django.contrib.auth.context_processors.auth'
        )

        return content

    def fix_django_patterns(self, content, file_path):
        """Fix Django URL patterns"""
        # Remove patterns() wrapper - simplified approach
        if 'patterns(' in content and 'urlpatterns' in content:
            # Match patterns('', ...) and extract content
            pattern = r"urlpatterns\s*=\s*patterns\(\s*['\"].*?['\"]\s*,\s*"
            if re.search(pattern, content):
                content = re.sub(pattern, 'urlpatterns = [', content)
                # Close with ] instead of )
                content = re.sub(r'\)\s*$', ']', content, count=1, flags=re.MULTILINE)
                self.log_change('patterns() removed')

        return content

    def print_stats(self):
        """Print upgrade statistics"""
        print(f"\n{'='*60}")
        print(f"Django Upgrade Statistics")
        print(f"{'='*60}")
        print(f"Files processed: {self.stats['files_processed']}")
        print(f"Files modified: {self.stats['files_modified']}")
        print(f"\nChanges by type:")
        for change, count in sorted(self.stats['changes'].items(), key=lambda x: -x[1]):
            print(f"  {change}: {count}")
        print(f"{'='*60}\n")

def main():
    upgrader = DjangoUpgrader()

    print("Starting Django 1.6 → 4.2 upgrade...")
    print("This will modify Python files in place.\n")

    for root, dirs, files in os.walk('.'):
        # Skip non-relevant directories
        dirs[:] = [d for d in dirs if d not in [
            '.git', '__pycache__', 'venv', 'cache', 'osqa_env',
            'tmp', 'log', 'upfiles'
        ]]

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                if upgrader.process_file(file_path):
                    print(f"Modified: {file_path}")

    upgrader.print_stats()

if __name__ == '__main__':
    main()
