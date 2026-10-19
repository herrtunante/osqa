from forum import settings

def application_settings(context):
    return {'settings': settings}

def csp_nonce(request):
    """The nonce inline scripts need under the Content-Security-Policy:
    <script nonce="{{ csp_nonce }}"> (see forum.middleware.security)."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}

