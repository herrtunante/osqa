"""Sentry error reporting.

init_server() is called from settings.py when SENTRY_DSN is set. For the
browser: the configuration the page passes to media/js/osqa.sentry.js, and the
origin the Content-Security-Policy must let it send reports to.
"""
from urllib.parse import urlsplit

from django.conf import settings as djsettings


def init_server(dsn, environment='', release='', traces_sample_rate=0.0, **options):
    """Returns False if sentry-sdk is not installed."""
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import ignore_logger
    except ImportError:
        return False
    sentry_sdk.init(
        dsn=dsn, environment=environment or None, release=release or None,
        traces_sample_rate=traces_sample_rate,
        # No cookies, request bodies, IP addresses or user details;
        # SentryUserMiddleware adds the user's numeric id.
        send_default_pii=False, max_request_body_size='never',
        **options)
    # Requests for other host names (scanners) are not errors of the forum.
    ignore_logger('django.security.DisallowedHost')
    # The 500 handler's own log entry (with the user and request headers, for
    # the log file) repeats the error Sentry already captured.
    ignore_logger('forum.errors')
    # Security log lines carry IP addresses and usernames; they stay in the log file.
    ignore_logger('forum.security')
    return True


def frontend_dsn():
    return str(getattr(djsettings, 'SENTRY_FRONTEND_DSN', '') or '').strip()


def dsn_origin(dsn):
    """'https://key@o1.ingest.sentry.io/2' -> 'https://o1.ingest.sentry.io'."""
    parts = urlsplit(dsn)
    if not parts.scheme or not parts.hostname:
        return ''
    port = ':%d' % parts.port if parts.port else ''
    return '%s://%s%s' % (parts.scheme, parts.hostname, port)


def browser_config(request):
    dsn = frontend_dsn()
    if not dsn_origin(dsn):
        return None
    user = getattr(request, 'user', None)
    return {
        'dsn': dsn,
        'environment': getattr(djsettings, 'SENTRY_ENVIRONMENT', ''),
        'release': getattr(djsettings, 'SENTRY_RELEASE', '') or None,
        'user': str(user.id) if user is not None and user.is_authenticated else None,
    }


def capture_csp_violation(directive, blocked, page):
    """Content blocked by the Content-Security-Policy, as a Sentry warning
    (grouped by directive and blocked address)."""
    if not getattr(djsettings, 'SENTRY_DSN', ''):
        return
    import sentry_sdk
    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = ['csp', directive, blocked]
        scope.set_tag('csp.directive', directive)
        scope.set_context('csp', {'blocked': blocked, 'page': page})
        sentry_sdk.capture_message('CSP blocked %s (%s)' % (blocked or 'inline code', directive), level='warning')
