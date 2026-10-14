"""Which of the forum's URLs the browser tests reach.

    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/coverage.py           # runs the suite, then reports
    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/coverage.py --report  # report on the last run only

Every page and AJAX request the browser makes is recorded (E2E_RECORD) and
resolved to its URL pattern; the report lists the patterns no test reached,
with how each was reached (GET pages, POST forms and commands).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = os.path.join(ROOT, 'tmp', 'e2e', 'requested.txt')

# Patterns that are not pages people use, or that the browser can't reach.
IGNORED = {
    'osqa_media', 'django_admin_media', 'django_admin_static', 'uploaded_file', 'custom_css', 'favicon',
    'csp_report',  # posted by the browser only when something is blocked
}


def run_suite(extra_args):
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    if os.path.exists(RECORD):
        os.remove(RECORD)
    env = dict(os.environ, E2E_RECORD=RECORD)
    return subprocess.call([sys.executable, 'manage.py', 'test', 'e2e', '--noinput'] + extra_args, cwd=ROOT, env=env)


def url_patterns():
    from django.urls import get_resolver, URLPattern, URLResolver

    def walk(patterns, namespace=''):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                ns = (pattern.namespace + ':') if pattern.namespace else namespace
                yield from walk(pattern.url_patterns, ns)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield namespace + pattern.name
    return sorted(set(walk(get_resolver().url_patterns)))


def report():
    sys.path.insert(0, ROOT)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    import django
    django.setup()
    from django.urls import resolve, Resolver404

    reached = {}
    for line in open(RECORD) if os.path.exists(RECORD) else []:
        method, _, path = line.strip().partition(' ')
        try:
            match = resolve(path)
        except Resolver404:
            continue
        name = (match.namespace + ':' if match.namespace else '') + (match.url_name or '')
        reached.setdefault(name, set()).add(method)

    names = [n for n in url_patterns() if n not in IGNORED and not n.startswith('admin:')]
    missing = [n for n in names if n not in reached]
    print('\nURL patterns reached by the browser tests: %d of %d (%d%%)' % (
        len(names) - len(missing), len(names), round(100.0 * (len(names) - len(missing)) / max(len(names), 1))))
    if missing:
        print('Not reached:')
        for name in missing:
            print('  ' + name)
    return missing


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--report']
    status = 0 if '--report' in sys.argv else run_suite(args)
    report()
    sys.exit(status)
