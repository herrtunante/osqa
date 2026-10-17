"""The OpenAPI 3.1 document of /api/v1/, built from the endpoint registry
(forum/api/core.py ENDPOINTS) and the schemas (forum/api/schemas.py), so it
can't drift from the URL patterns and handlers. Served at
/api/v1/openapi.json; the docs page (/api/) is rendered from the same data."""
import copy

from forum import settings

from . import endpoints  # noqa: F401  (registers the endpoints)
from .core import BASE_PATH, ENDPOINTS, PROBLEMS, problem_type
from .schemas import COMPONENTS, ref

GROUPS = [
    ('About', 'The index and this document.'),
    ('Questions', 'Lists, search, one question with its answers and comments; asking and following.'),
    ('Answers', 'Answers of a question, one answer; answering and accepting.'),
    ('Comments', 'Commenting on questions and answers.'),
    ('Votes', 'Voting on questions and answers.'),
    ('Tags', 'Tags and their synonyms.'),
    ('Members', 'Public profiles, badges and the token owner.'),
]

AUTH_TEXT = {
    'none': 'No token.',
    'optional': 'Token optional (with one you also see your own posts waiting for review).',
    'read': 'Needs a token.',
    'write': 'Needs a token with the write scope.',
}

RATE_HEADERS = {
    'X-RateLimit-Limit': {'description': 'Requests allowed per minute.', 'schema': {'type': 'integer'}},
    'X-RateLimit-Remaining': {'description': 'Requests left in this minute.', 'schema': {'type': 'integer'}},
    'X-RateLimit-Reset': {'description': 'Seconds until the minute resets.', 'schema': {'type': 'integer'}},
}


def _schema(value):
    return ref(value) if isinstance(value, str) else value


def _security(auth):
    if auth == 'none':
        return []
    if auth == 'optional':
        return [{}, {'bearerAuth': ['read']}]
    return [{'bearerAuth': ['write' if auth == 'write' else 'read']}]


def operation(ep):
    op = {
        'operationId': ep.operation_id,
        'summary': ep.summary,
        'description': ' '.join(filter(None, [ep.description, AUTH_TEXT[ep.auth]])),
        'tags': [ep.group],
        'security': _security(ep.auth),
    }
    params = []
    for p in ep.params:
        param = {'name': p.name, 'in': p.where, 'required': p.required, 'description': p.description,
                 'schema': p.schema}
        if p.example is not None:
            param['example'] = p.example
        params.append(param)
    if params:
        op['parameters'] = params
    if ep.body:
        body = {'required': True, 'content': {'application/json': {'schema': _schema(ep.body)}}}
        if ep.body_example:
            body['content']['application/json']['example'] = ep.body_example
        op['requestBody'] = body
    success = {'description': 'Created.' if ep.status == 201 else 'OK.',
               'content': {'application/json': {'schema': _schema(ep.response)}}}
    headers = dict(RATE_HEADERS) if ep.auth != 'none' else {}
    if ep.status == 201:
        headers['Location'] = {'description': 'URL of the new object.', 'schema': {'type': 'string'}}
    if ep.etag:
        headers['ETag'] = {'description': 'Send it back in If-None-Match to get 304 when nothing changed.',
                           'schema': {'type': 'string'}}
    if ep.paginated:
        headers['Link'] = {'description': 'rel="next": the next page, as in the body\'s next.',
                           'schema': {'type': 'string'}}
    if headers:
        success['headers'] = headers
    responses = {str(ep.status): success}
    if ep.etag:
        responses['304'] = {'description': 'Not modified (If-None-Match matched the ETag).'}
    for slug in ep.errors:
        status = str(PROBLEMS[slug][0])
        entry = responses.setdefault(status, {'description': '', 'content': {
            'application/problem+json': {'schema': ref('Problem')}}})
        entry['description'] = ', '.join(filter(None, [entry['description'], PROBLEMS[slug][1]]))
    op['responses'] = responses
    return op


def openapi_path(path):
    return BASE_PATH + (path if path != '/' else '/')


def document():
    paths = {}
    for ep in ENDPOINTS:
        paths.setdefault(openapi_path(ep.path), {})[ep.method.lower()] = operation(ep)
    return {
        'openapi': '3.1.0',
        'jsonSchemaDialect': 'https://spec.openapis.org/oas/3.1/dialect/base',
        'info': {
            'title': '%s API' % settings.APP_SHORT_NAME,
            'version': '1.0',
            'summary': 'Read the questions, answers, tags and members of the forum, and post with a personal access '
                       'token.',
            'description': 'Lists are {"data": [...], "next": url or null} with the same URL in a Link header '
                           '(rel="next"); single objects are returned as they are. Dates are ISO 8601 with the '
                           'site\'s UTC offset. Errors are RFC 9457 problem details (application/problem+json); '
                           'their types are explained at %s/api/#problems.' % settings.APP_BASE_URL,
        },
        'servers': [{'url': settings.APP_BASE_URL}],
        'tags': [{'name': name, 'description': text} for name, text in GROUPS],
        'paths': paths,
        'components': {
            'schemas': copy.deepcopy(COMPONENTS),
            'securitySchemes': {'bearerAuth': {
                'type': 'http', 'scheme': 'bearer',
                'description': 'A personal access token, created on your account page (Settings > API tokens). '
                               'Scopes: read, write.'}},
        },
        'externalDocs': {'url': '%s/api/' % settings.APP_BASE_URL, 'description': 'API documentation'},
        'x-problem-types': {slug: {'type': problem_type(slug), 'status': status, 'title': title}
                            for slug, (status, title) in PROBLEMS.items()},
    }
