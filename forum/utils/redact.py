"""Helpers to keep secrets out of logged request data."""

REDACTED = '********'

SENSITIVE_HEADERS = ('HTTP_COOKIE', 'HTTP_AUTHORIZATION', 'HTTP_X_CSRFTOKEN')


def _is_sensitive_field(name):
    name = name.lower()
    return 'password' in name or 'csrf' in name or 'token' in name or 'secret' in name


def redact_params(params):
    """Items of a QueryDict (POST/GET) with password and token fields hidden."""
    return [(k, REDACTED if _is_sensitive_field(k) else v) for k, v in params.items()]


def redact_meta(meta):
    """Items of request.META with cookie and authorization headers hidden."""
    return [(k, REDACTED if k in SENSITIVE_HEADERS else v) for k, v in meta.items()]
