"""The machinery of the JSON API: the endpoint registry (the single source of
the URL patterns, the dispatcher and the OpenAPI document), RFC 9457 problem
responses, token authentication, rate limits, CORS, conditional GET and
cursor pagination. The endpoints themselves are in forum/api/endpoints.py.

Requests are authenticated by a personal access token only
(`Authorization: Bearer osqa_...`, forum/models/api.py); the session cookie is
never used, so a browser that is signed in reads the API as an anonymous
visitor and can't write through it (no CSRF exposure). Writes need a token
with the write scope.
"""
import base64
import binascii
import datetime
import hashlib
import json
import logging
import re
import time

from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from forum import settings
from forum.utils import metrics, security_log, throttle

log = logging.getLogger('forum.api')

VERSION = 'v1'
BASE_PATH = '/api/%s' % VERSION
DEFAULT_LIMIT = 30
MAX_LIMIT = 100
MAX_BODY = 256 * 1024
RATE_WINDOW = 60

metrics.HELP.setdefault('osqa_api_writes_total', ('counter', 'Changes made through the API, by endpoint.'))
metrics.HELP.setdefault('osqa_api_rate_limited_total', ('counter', 'API requests refused by the rate limits.'))


# ---- problems (RFC 9457) -------------------------------------------------

# slug: (status, title). The type of a problem is <site>/api/#problem-<slug>.
PROBLEMS = {
    'bad-request': (400, 'Bad request'),
    'unauthorized': (401, 'Authentication required'),
    'invalid-token': (401, 'Invalid token'),
    'insufficient-scope': (403, 'Insufficient scope'),
    'forbidden': (403, 'Not allowed'),
    'account-suspended': (403, 'Account suspended'),
    'email-confirmation-required': (403, 'Email confirmation required'),
    'captcha-required': (403, 'Use the web site'),
    'writes-disabled': (403, 'Writing through the API is turned off'),
    'not-found': (404, 'Not found'),
    'method-not-allowed': (405, 'Method not allowed'),
    'conflict': (409, 'Conflict'),
    'payload-too-large': (413, 'Request body too large'),
    'unsupported-media-type': (415, 'Unsupported media type'),
    'validation-error': (422, 'Invalid data'),
    'spam': (422, 'Looks like spam'),
    'rate-limited': (429, 'Too many requests'),
    'posting-limit': (429, 'Posting limit reached'),
    'server-error': (500, 'Server error'),
    'api-disabled': (503, 'The API is turned off'),
}


class Problem(Exception):
    def __init__(self, slug, detail='', headers=None, **extra):
        super().__init__(detail)
        self.slug = slug
        self.status, self.title = PROBLEMS[slug]
        self.detail = str(detail)
        self.headers = headers or {}
        self.extra = extra


def problem_type(slug):
    return '%s/api/#problem-%s' % (settings.APP_BASE_URL, slug)


def problem_response(request, problem):
    data = {'type': problem_type(problem.slug), 'title': problem.title, 'status': problem.status}
    if problem.detail:
        data['detail'] = problem.detail
    data['instance'] = request.path
    data.update(problem.extra)
    response = HttpResponse(json.dumps(data, ensure_ascii=False), status=problem.status,
                            content_type='application/problem+json; charset=utf-8')
    for name, value in problem.headers.items():
        response[name] = value
    return response


# ---- the registry --------------------------------------------------------

class Param:
    def __init__(self, name, where, schema, description='', required=False, example=None):
        self.name, self.where, self.schema = name, where, schema
        self.description, self.required, self.example = description, required or where == 'path', example


class Endpoint:
    """One operation. `auth`: 'none' (never a token), 'optional' (a token
    shows what its owner may see), 'read' (a token is required) or 'write'
    (a token with the write scope). `response`: a schema ($ref name or
    dict) of the 2xx body; `body`: the JSON request body's schema."""

    def __init__(self, method, path, handler, operation_id, summary, description='', group='', auth='optional',
                 params=(), body=None, body_example=None, response=None, status=200, etag=False, errors=(),
                 paginated=False):
        self.method, self.path, self.handler = method, path, handler
        self.operation_id, self.summary, self.description = operation_id, summary, description
        self.group, self.auth, self.params = group, auth, list(params)
        self.body, self.body_example, self.response, self.status = body, body_example, response, status
        self.etag, self.errors, self.paginated = etag, tuple(errors), paginated

    @property
    def is_write(self):
        return self.auth == 'write'

    @property
    def path_params(self):
        return [p for p in self.params if p.where == 'path']

    @property
    def query_params(self):
        return [p for p in self.params if p.where == 'query']


ENDPOINTS = []


def endpoint(method, path, **options):
    """Registers the decorated function as the handler of `method path`."""
    def register(fn):
        ENDPOINTS.append(Endpoint(method, path, fn, operation_id=options.pop('operation_id', fn.__name__), **options))
        return fn
    return register


def routes():
    """{path: {method: Endpoint}} in registration order."""
    found = {}
    for ep in ENDPOINTS:
        found.setdefault(ep.path, {})[ep.method] = ep
    return found


def path_regex(path, params):
    """'/questions/{id}' -> '^questions/(?P<id>\\d+)/?$' (relative to /api/v1/)."""
    types = {p.name: p.schema.get('type') for p in params if p.where == 'path'}

    def group(name):
        return '(?P<%s>%s)' % (name, r'\d+' if types.get(name) == 'integer' else r'[^/]+')

    body = ''.join(group(part[1:-1]) if part.startswith('{') else re.escape(part)
                   for part in re.split(r'(\{\w+\})', path.strip('/')))
    return '^%s/?$' % body if body else '^$'


def route_name(path):
    """URL pattern name of a path: 'api' + its parts ('/questions/{id}/answers' -> 'api_questions_id_answers')."""
    parts = [p.strip('{}').replace('.', '_') for p in path.strip('/').split('/') if p]
    return '_'.join(['api'] + parts) if parts else 'api_root'


# ---- small helpers -------------------------------------------------------

def absolute(url):
    return url if '://' in url else '%s%s' % (settings.APP_BASE_URL, url)


def api_url(path):
    return absolute(BASE_PATH + path)


def iso(value):
    """ISO 8601 with the site's UTC offset (dates are stored in TIME_ZONE)."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_default_timezone())
        return value.isoformat(timespec='seconds')
    return value.isoformat()


def parse_since(value):
    """A naive local datetime from 'YYYY-MM-DD' or an ISO 8601 date and time."""
    value = str(value).strip().replace('Z', '+00:00').replace(' ', '+')
    try:
        if len(value) == 10:
            return datetime.datetime.combine(datetime.date.fromisoformat(value), datetime.time.min)
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise Problem('bad-request', _("since must be a date (YYYY-MM-DD) or an ISO 8601 date and time."),
                      errors={'since': [_("Not a date.")]})
    if timezone.is_aware(moment):
        moment = timezone.make_naive(moment, timezone.get_default_timezone())
    return moment


def int_param(request, name, default, minimum, maximum):
    raw = request.GET.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not minimum <= value <= maximum:
        raise Problem('bad-request', _("%(name)s must be a whole number from %(min)d to %(max)d.") % {
            'name': name, 'min': minimum, 'max': maximum}, errors={name: [_("Out of range.")]})
    return value


def choice_param(request, name, choices, default=''):
    value = request.GET.get(name, '').strip().lower() or default
    if value and value not in choices:
        raise Problem('bad-request', _("%(name)s must be one of: %(choices)s.") % {
            'name': name, 'choices': ', '.join(choices)}, errors={name: [_("Unknown value.")]})
    return value


def json_body(request):
    """The request's JSON object."""
    content_type = request.META.get('CONTENT_TYPE', '').split(';')[0].strip().lower()
    if content_type != 'application/json':
        raise Problem('unsupported-media-type', _("Send the request body as JSON (Content-Type: application/json)."))
    if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BODY:
        raise Problem('payload-too-large', _("The request body is larger than %d KB.") % (MAX_BODY // 1024))
    try:
        data = json.loads(request.body.decode('utf-8') or 'null')
    except (ValueError, UnicodeDecodeError):
        raise Problem('bad-request', _("The request body is not valid JSON."))
    if not isinstance(data, dict):
        raise Problem('bad-request', _("The request body must be a JSON object."))
    return data


def text_field(data, name, required=True):
    value = data.get(name, '')
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise Problem('validation-error', _("%s must be a string.") % name, errors={name: [_("Must be a string.")]})
    if required and not value.strip():
        raise Problem('validation-error', _("%s is required.") % name, errors={name: [_("This field is required.")]})
    return value


# ---- cursor pagination ---------------------------------------------------

class Key:
    """One column of a keyset ordering: `field` (attribute of the rows),
    descending or not, and the kind of its values ('datetime', 'int', 'str')."""

    def __init__(self, field, descending=True, kind='int'):
        self.field, self.descending, self.kind = field, descending, kind

    def order(self):
        return ('-' if self.descending else '') + self.field

    def encode(self, value):
        return value.isoformat() if self.kind == 'datetime' and value is not None else value

    def decode(self, value):
        if self.kind == 'datetime':
            return datetime.datetime.fromisoformat(value)
        if self.kind == 'int':
            return int(value)
        if not isinstance(value, str):
            raise ValueError(value)
        return value


def _encode_cursor(sort, values):
    raw = json.dumps([sort, values], separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _decode_cursor(cursor, sort, keys):
    try:
        raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
        cursor_sort, values = json.loads(raw.decode('utf-8'))
        if cursor_sort != sort or len(values) != len(keys):
            raise ValueError
        return [key.decode(value) for key, value in zip(keys, values)]
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        raise Problem('bad-request', _("The cursor is not valid for this list; use the next link of a previous page."),
                      errors={'cursor': [_("Not valid.")]})


def _after(keys, values):
    """Q for the rows after `values` in the ordering of `keys`."""
    from django.db.models import Q
    condition = Q(pk__in=[])
    for i, key in enumerate(keys):
        step = Q(**{'%s__%s' % (key.field, 'lt' if key.descending else 'gt'): values[i]})
        for previous, value in zip(keys[:i], values[:i]):
            step &= Q(**{previous.field: value})
        condition |= step
    return condition


def next_link(request, cursor):
    params = request.GET.copy()
    params['cursor'] = cursor
    return '%s%s?%s' % (settings.APP_BASE_URL, request.path, params.urlencode())


def page(request, queryset, keys, sort, prepare=None):
    """(rows, next url or None) of `queryset` ordered by `keys` (the last one
    unique), from the `cursor` parameter. `prepare(rows)` may load related
    data for the page in bulk (prefetch_cached) before it is serialized."""
    limit = int_param(request, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT)
    queryset = queryset.order_by(*[key.order() for key in keys])
    cursor = request.GET.get('cursor', '').strip()
    if cursor:
        queryset = queryset.filter(_after(keys, _decode_cursor(cursor, sort, keys)))
    rows = list(queryset[:limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    if prepare is not None:
        rows = prepare(rows)
    following = None
    if more and rows:
        last = rows[-1]
        following = next_link(request, _encode_cursor(sort, [key.encode(getattr(last, key.field)) for key in keys]))
    return rows, following


class ListResult:
    """A list response: {"data": [...], "next": url or null} and a Link header."""

    def __init__(self, data, next_url=None):
        self.data, self.next_url = data, next_url


class Created:
    """A 201 response with a Location header."""

    def __init__(self, data, location):
        self.data, self.location = data, location


# ---- authentication -------------------------------------------------------

class _DiscardedMessages:
    """Actions show messages to "the current request" (messages.info); an API
    client never sees them, and they must not set a cookie."""
    used = True

    def add(self, *args, **kwargs):
        return None

    def update(self, response):
        return None

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0


def bearer_token(request):
    """The token of the Authorization header ('' without one)."""
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not header:
        return ''
    scheme, _space, value = header.partition(' ')
    if scheme.lower() != 'bearer' or not value.strip():
        raise Problem('invalid-token', _("Send the token as \"Authorization: Bearer <token>\"."),
                      headers={'WWW-Authenticate': 'Bearer realm="api", error="invalid_request"'})
    return value.strip()


def authenticate(request):
    """Sets request.user and request.api_token from the bearer token
    (an anonymous user without one)."""
    from forum.models import ApiToken
    from forum.models.user import AnonymousUser
    from forum.utils import accounts
    request.user = AnonymousUser()
    request.api_token = None
    raw = bearer_token(request)
    if not raw:
        return
    token = ApiToken.objects.find(raw)
    invalid = {'WWW-Authenticate': 'Bearer realm="api", error="invalid_token"'}
    if token is None:
        security_log.event('api_token_invalid', request, reason='unknown')
        raise Problem('invalid-token', _("This token is not valid. It may have been revoked."), headers=invalid)
    if token.is_expired:
        security_log.event('api_token_invalid', request, reason='expired', token_id=token.id, account=token.user)
        raise Problem('invalid-token', _("This token expired on %s. Create a new one on your account page.") %
                      token.expires.strftime('%Y-%m-%d'), headers=invalid)
    user = token.user
    if user.is_suspended() or not user.is_active:
        security_log.event('api_token_refused', request, reason='suspended', token_id=token.id, account=user)
        raise Problem('account-suspended', _("This account is suspended, so its tokens can't be used."))
    if accounts.must_confirm_to_sign_in(user):
        raise Problem('email-confirmation-required', _("Confirm your email address before using the API."))
    token.touch(request.META.get('REMOTE_ADDR', ''))
    request.user = user
    request.api_token = token


def check_access(request, ep):
    token = request.api_token
    if ep.is_write and not bool(settings.API_WRITES_ENABLED):
        raise Problem('writes-disabled', _("This site doesn't accept changes through the API at the moment."))
    if ep.auth in ('read', 'write') and token is None:
        raise Problem('unauthorized', _("This needs a personal access token: send \"Authorization: Bearer <token>\"."),
                      headers={'WWW-Authenticate': 'Bearer realm="api"'})
    needed = 'write' if ep.is_write else 'read'
    if token is not None and not token.has_scope(needed):
        raise Problem('insufficient-scope', _("This token doesn't have the %s scope.") % needed,
                      headers={'WWW-Authenticate': 'Bearer realm="api", error="insufficient_scope", scope="%s"'
                                                   % needed})


# ---- rate limits ------------------------------------------------------------

def _hit(bucket, ident, limit):
    """(limit, remaining, seconds to reset, over) for one request in the
    current minute (windows aligned to the clock), or None without a limit."""
    if limit <= 0:
        return None
    now = time.time()
    start = int(now // RATE_WINDOW) * RATE_WINDOW
    count = throttle.hit('api:%s:%d' % (bucket, start), ident, RATE_WINDOW + 5)
    return limit, max(0, limit - count), max(1, int(start + RATE_WINDOW - now + 0.999)), count > limit


def rate_limit(request, ep):
    """Counts the request; returns the headers to send, or raises a 429 problem."""
    token = request.api_token
    if token is not None:
        results = [_hit('token', token.id, int(settings.API_RATE_LIMIT_TOKEN))]
        if ep.is_write:
            results.append(_hit('write', token.id, int(settings.API_WRITE_RATE_LIMIT)))
    else:
        results = [_hit('ip', throttle.client_ip(request), int(settings.API_RATE_LIMIT_ANONYMOUS))]
    results = [r for r in results if r is not None]
    if not results:
        return {}
    over = [r for r in results if r[3]]
    shown = over[0] if over else min(results, key=lambda r: r[1])
    headers = {'X-RateLimit-Limit': str(shown[0]), 'X-RateLimit-Remaining': str(shown[1]),
               'X-RateLimit-Reset': str(shown[2])}
    if over:
        metrics.count('osqa_api_rate_limited_total', kind='token' if token is not None else 'ip')
        headers['Retry-After'] = str(shown[2])
        raise Problem('rate-limited', _("Too many requests: at most %(limit)d per minute. Try again in %(seconds)d "
                                        "seconds.") % {'limit': shown[0], 'seconds': shown[2]}, headers=headers)
    return headers


# ---- CORS -------------------------------------------------------------------

EXPOSED_HEADERS = 'ETag, Link, Location, Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset'


def cors_headers(request):
    """Headers that let other sites read the API with JavaScript (GET only,
    never with cookies: no Access-Control-Allow-Credentials)."""
    origins = str(settings.API_CORS_ORIGINS or '').split()
    origin = request.META.get('HTTP_ORIGIN', '')
    if not origins or not origin:
        return {}
    if '*' in origins:
        allowed = '*'
    elif origin in origins:
        allowed = origin
    else:
        return {}
    return {'Access-Control-Allow-Origin': allowed, 'Access-Control-Expose-Headers': EXPOSED_HEADERS}


def preflight(request, allowed_methods):
    response = HttpResponse(status=204)
    response['Allow'] = ', '.join(allowed_methods)
    cors = cors_headers(request)
    if cors and request.META.get('HTTP_ACCESS_CONTROL_REQUEST_METHOD', 'GET').upper() in ('GET', 'HEAD'):
        for name, value in cors.items():
            response[name] = value
        response['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Authorization, If-None-Match'
        response['Access-Control-Max-Age'] = '600'
    response['Vary'] = 'Origin'
    return response


# ---- the view ---------------------------------------------------------------

def json_response(data, status=200):
    return HttpResponse(json.dumps(data, ensure_ascii=False), status=status,
                        content_type='application/json; charset=utf-8')


def _render(request, ep, result):
    if isinstance(result, HttpResponse):
        return result
    if isinstance(result, ListResult):
        response = json_response({'data': result.data, 'next': result.next_url})
        if result.next_url:
            response['Link'] = '<%s>; rel="next"' % result.next_url
        return response
    if isinstance(result, Created):
        response = json_response(result.data, status=201)
        response['Location'] = result.location
        return response
    return json_response(result, status=ep.status)


def _etag(request, response):
    """A strong ETag of the body; 304 when the client has it."""
    tag = '"%s"' % hashlib.sha256(response.content).hexdigest()[:32]
    if tag in [t.strip() for t in request.META.get('HTTP_IF_NONE_MATCH', '').split(',')] or \
            request.META.get('HTTP_IF_NONE_MATCH', '').strip() == '*':
        response = HttpResponse(status=304)
    response['ETag'] = tag
    return response


def _finish(request, response, extra_headers, read):
    for name, value in extra_headers.items():
        response[name] = value
    if read:
        for name, value in cors_headers(request).items():
            response[name] = value
        response['Cache-Control'] = '%s, no-cache' % ('private' if request.api_token is not None else 'public')
    else:
        response['Cache-Control'] = 'no-store'
    response['Vary'] = 'Authorization, Origin, Accept-Language'
    response['X-Robots-Tag'] = 'noindex'
    if request.method == 'HEAD':
        response.content = b''
    return response


@csrf_exempt
def dispatch(request, route, **kwargs):
    """The one view of every /api/v1/ URL: finds the endpoint of the path and
    method, authenticates, counts the rate limit and renders the result."""
    request._messages = _DiscardedMessages()
    request.api_token = None
    methods = routes().get(route, {})
    method = 'GET' if request.method == 'HEAD' else request.method
    allowed = sorted(set(methods) | ({'HEAD'} if 'GET' in methods else set()) | {'OPTIONS'})
    read = method == 'GET'
    headers = {}
    try:
        if not bool(settings.API_ENABLED):
            raise Problem('api-disabled', _("The administrators of this site have turned the API off."))
        if method == 'OPTIONS':
            return preflight(request, allowed)
        ep = methods.get(method)
        if ep is None:
            raise Problem('method-not-allowed', _("%(method)s is not allowed here; use %(allowed)s.") % {
                'method': request.method, 'allowed': ', '.join(allowed)}, headers={'Allow': ', '.join(allowed)})
        if ep.auth != 'none':
            authenticate(request)
            check_access(request, ep)
        headers = rate_limit(request, ep)
        response = _render(request, ep, ep.handler(request, **kwargs))
        if ep.is_write and 200 <= response.status_code < 300:
            metrics.count('osqa_api_writes_total', endpoint=ep.operation_id)
        if ep.etag and read and response.status_code == 200:
            response = _etag(request, response)
    except Problem as problem:
        response = problem_response(request, problem)
    except Http404:
        response = problem_response(request, Problem('not-found', _("There is nothing here, or you may not see it.")))
    except Exception:
        log.exception('API request failed: %s %s', request.method, request.path)
        response = problem_response(request, Problem('server-error', _("Something went wrong on our side.")))
    return _finish(request, response, headers, read)
