"""Request and response handling for running safely behind a proxy.

ClientIPMiddleware (first in MIDDLEWARE): behind a reverse proxy REMOTE_ADDR is
the proxy; with TRUSTED_PROXY_COUNT = n it is replaced by the address the
n-th proxy from the right saw in X-Forwarded-For, so sign-up limits, sign-in
lockouts, action IPs and the maintenance allow-list see the real visitor.
It also answers /healthz before host checks and maintenance mode, so Docker
or a load balancer can probe it on any host name.

SecurityHeadersMiddleware adds Content-Security-Policy and Permissions-Policy.
The templates still use inline scripts, so the policy allows inline code and
gets its value from limiting where scripts, frames and plugins may come from
and who may frame the site.
"""
from django.conf import settings as djsettings
from django.db import connection
from django.http import HttpResponse
from django.urls import reverse

GOOGLE = 'https://www.google.com https://*.google.com https://www.gstatic.com'
ANALYTICS = 'https://ssl.google-analytics.com https://www.google-analytics.com'

PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()'


class ClientIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        proxies = int(getattr(djsettings, 'TRUSTED_PROXY_COUNT', 0))
        if proxies:
            forwarded = [ip.strip() for ip in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if ip.strip()]
            if forwarded:
                request.META['REMOTE_ADDR'] = forwarded[-min(proxies, len(forwarded))]
        if request.path_info == '/healthz':
            return health(request)
        return self.get_response(request)


def health(request):
    """200 'ok' when the database answers, 503 otherwise."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return HttpResponse('database unavailable\n', status=503, content_type='text/plain')
    return HttpResponse('ok\n', content_type='text/plain')


def content_security_policy(request):
    from forum import settings
    configured = getattr(djsettings, 'CONTENT_SECURITY_POLICY', '')
    if configured is None:
        return None
    if configured:
        return configured
    from forum.utils.sentry import frontend_dsn, dsn_origin
    extra = ' '.join(str(settings.CSP_EXTRA_SOURCES).split())
    with_extra = lambda sources: ('%s %s' % (sources, extra)).strip()
    sentry = dsn_origin(frontend_dsn())  # where browser errors are reported
    policy = [
        "default-src 'self'",
        "script-src " + with_extra("'self' 'unsafe-inline' %s %s" % (GOOGLE, ANALYTICS)),
        "style-src " + with_extra("'self' 'unsafe-inline' %s https://fonts.googleapis.com" % GOOGLE),
        "font-src " + with_extra("'self' data: https://fonts.gstatic.com"),
        "connect-src " + with_extra(("'self' %s %s %s" % (GOOGLE, ANALYTICS, sentry)).strip()),
        "frame-src " + with_extra("'self' %s" % GOOGLE),
        # Posts may show images from anywhere.
        "img-src * data: blob:",
        "media-src *",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'self'",
        "report-uri " + reverse('csp_report'),
    ]
    if request.is_secure():
        policy.append('upgrade-insecure-requests')
    return '; '.join(policy)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            policy = content_security_policy(request)
            if policy:
                header = 'Content-Security-Policy-Report-Only' if \
                    getattr(djsettings, 'CONTENT_SECURITY_POLICY_REPORT_ONLY', False) else 'Content-Security-Policy'
                response.headers.setdefault(header, policy)
        response.headers.setdefault('Permissions-Policy', PERMISSIONS_POLICY)
        return response
