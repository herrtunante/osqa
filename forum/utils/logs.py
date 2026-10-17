"""Request ids and the optional JSON log format.

Every request gets an id: the X-Request-ID header set by the proxy (nginx
`proxy_set_header X-Request-ID $request_id;`, most load balancers), when it is
a plain token of at most 128 characters, or a new random one. The response
carries it back in X-Request-ID, Sentry events get it as the `request_id`
tag, and log records get it as `request_id` (RequestContextFilter), so one
failing request can be followed from the proxy's access log to the
application log and Sentry.

LOG_FORMAT = 'json' (settings_local.py, or the LOG_FORMAT environment
variable) replaces LOGGING with one JSON object per line on standard output,
for containers: time, level, logger, message, request_id, user_id (a numeric
id, never a name) and the traceback as `exception`. Email addresses, IP
addresses and password/token/secret values are masked in the message and
traceback of every logger except forum.security, whose lines are the
security log (they name the IP address, the account and, for refused
sign-in links, the address typed; see forum/utils/security_log.py).
"""
import contextvars
import datetime
import ipaddress
import json
import logging
import re
import uuid

_request = contextvars.ContextVar('osqa_request', default=None)
_request_id = contextvars.ContextVar('osqa_request_id', default=None)

REQUEST_ID_HEADER = 'X-Request-ID'
_VALID_ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


def new_request_id():
    return uuid.uuid4().hex


def request_id_from(request):
    """The proxy's X-Request-ID when it looks like an id, else a new one."""
    supplied = request.META.get('HTTP_X_REQUEST_ID', '').strip()
    return supplied if _VALID_ID.match(supplied) else new_request_id()


def bind_request(request, request_id):
    """Makes the request current for logging; returns tokens for unbind_request()."""
    return _request.set(request), _request_id.set(request_id)


def unbind_request(tokens):
    _request.reset(tokens[0])
    _request_id.reset(tokens[1])


def current_request_id():
    return _request_id.get()


def current_user_id():
    """The signed-in user's id, only if the request already loaded the user
    (reading it must not start a session or database query inside logging)."""
    request = _request.get()
    if request is None:
        return None
    user = request.__dict__.get('user')
    if user is None:
        return None
    from django.utils.functional import empty
    wrapped = getattr(user, '_wrapped', user)  # a SimpleLazyObject not evaluated yet: skip
    if wrapped is None or wrapped is empty:
        return None
    try:
        return wrapped.id if wrapped.is_authenticated else None
    except Exception:
        return None


class RequestContextFilter(logging.Filter):
    """Adds request_id and user_id to every record (None outside requests)."""

    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = current_request_id()
        if not hasattr(record, 'user_id'):
            record.user_id = current_user_id()
        return True


# --- masking ---------------------------------------------------------------

MASK = '[redacted]'
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
_IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_IPV6_CANDIDATE = re.compile(r'(?<![0-9A-Za-z:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![0-9A-Za-z:])')
_SECRET_VALUE = re.compile(
    r'(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|authorization|sessionid|csrftoken|cookie)'
    r'''(["']?\s*[:=]\s*["']?)(bearer\s+|basic\s+)?([^\s,;&"'<>]+)''')


def _mask_ipv4(match):
    try:
        ipaddress.IPv4Address(match.group(0))
    except ValueError:
        return match.group(0)
    return '[ip]'


def _mask_ipv6(match):
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return '[ip]'


def mask(text):
    """Text with email addresses, IP addresses and secret values replaced."""
    if not text:
        return text
    text = _SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    text = _EMAIL.sub('[email]', text)
    text = _IPV4.sub(_mask_ipv4, text)
    return _IPV6_CANDIDATE.sub(_mask_ipv6, text)


class JSONFormatter(logging.Formatter):
    """One JSON object per line."""

    # Loggers whose lines are allowed to carry IPs and usernames (documented).
    UNMASKED = ('forum.security',)

    def format(self, record):
        message = record.getMessage()
        exception = self.formatException(record.exc_info) if record.exc_info else None
        if record.stack_info:
            exception = ((exception + '\n') if exception else '') + self.formatStack(record.stack_info)
        if not any(record.name == n or record.name.startswith(n + '.') for n in self.UNMASKED):
            message, exception = mask(message), mask(exception)
        entry = {
            'time': datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'message': message,
            'request_id': getattr(record, 'request_id', None) or current_request_id(),
            'user_id': getattr(record, 'user_id', None) or current_user_id(),
        }
        if exception:
            entry['exception'] = exception
        return json.dumps(entry, ensure_ascii=False, default=str)


def json_logging(level='INFO'):
    """The LOGGING dictionary used when LOG_FORMAT = 'json'."""
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {'request': {'()': 'forum.utils.logs.RequestContextFilter'}},
        'formatters': {'json': {'()': 'forum.utils.logs.JSONFormatter'}},
        'handlers': {
            'stdout': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout',
                       'formatter': 'json', 'filters': ['request']},
        },
        'loggers': {
            # The security log keeps INFO even if the root level is raised.
            'forum.security': {'handlers': ['stdout'], 'level': 'INFO', 'propagate': False},
            # Django's request logger repeats 4xx/5xx with the request; the 500
            # handler (forum.errors) and Sentry already report errors.
            'django.request': {'handlers': ['stdout'], 'level': 'ERROR', 'propagate': False},
        },
        'root': {'handlers': ['stdout'], 'level': level},
    }
