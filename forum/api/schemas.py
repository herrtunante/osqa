"""JSON Schemas (2020-12, as OpenAPI 3.1 uses them) of what the API returns
and accepts. forum/api/openapi.py puts them in components/schemas; the tests
check real responses against them."""

STR = {'type': 'string'}
INT = {'type': 'integer'}
BOOL = {'type': 'boolean'}
URL = {'type': 'string', 'format': 'uri'}
DATETIME = {'type': 'string', 'format': 'date-time'}


def ref(name):
    return {'$ref': '#/components/schemas/%s' % name}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def obj(properties, required=None, description=None):
    schema = {'type': 'object', 'properties': properties,
              'required': list(required if required is not None else properties)}
    if description:
        schema['description'] = description
    return schema


def page_of(name):
    return obj({
        'data': {'type': 'array', 'items': ref(name)},
        'next': nullable(URL),
    }, description='A list: the items, and the URL of the next page (null on the last page).')


COMPONENTS = {
    'UserBrief': obj({
        'id': INT, 'username': STR, 'url': URL, 'reputation': INT,
    }, description='A member, as shown next to posts.'),
    'User': obj({
        'id': INT, 'username': STR, 'url': URL, 'api_url': URL, 'reputation': INT,
        'real_name': nullable(STR), 'location': nullable(STR),
        'website': nullable({'type': 'string', 'description': 'Shown only once the member has enough reputation.'}),
        'badges': obj({'gold': INT, 'silver': INT, 'bronze': INT}),
        'joined': DATETIME, 'moderator': BOOL,
    }, description='A public profile.'),
    'Me': obj({
        'user': ref('User'),
        'token': obj({'name': STR, 'scopes': {'type': 'array', 'items': {'enum': ['read', 'write']}},
                      'expires': nullable(DATETIME)}),
    }),
    'QuestionSummary': obj({
        'id': INT, 'url': URL, 'api_url': URL, 'title': STR,
        'tags': {'type': 'array', 'items': STR},
        'score': INT,
        'status': {'enum': ['unanswered', 'answered', 'accepted']},
        'answer_count': INT, 'view_count': INT, 'closed': BOOL,
        'duplicate_of': nullable(obj({'id': INT, 'url': URL, 'api_url': URL})),
        'author': nullable(ref('UserBrief')),
        'created': DATETIME, 'last_activity': DATETIME,
        'held_for_review': {'type': 'boolean', 'description': 'Waiting for a moderator (shown to its author and '
                            'moderators only; nobody else sees the post).'},
    }),
    'PostVersion': obj({
        'tag': STR,
        'version': {'type': 'string', 'description': 'The version of the tagged product the post applies to.'},
        'current_version': nullable({'type': 'string', 'description': "The tag's current version."}),
        'outdated': {'type': 'boolean', 'description': 'A newer version than the one it was written (or confirmed) '
                     'for is current.'},
        'confirmed_for': nullable(obj({'version': STR, 'by': nullable(ref('UserBrief')), 'at': nullable(DATETIME)},
                                      description='Confirmed to still work in a newer version, without editing.')),
    }, description='The version of a tagged product a post applies to.'),
    'Comment': obj({
        'id': INT, 'post_id': INT, 'url': URL, 'score': INT, 'author': nullable(ref('UserBrief')),
        'created': DATETIME, 'html': STR, 'markdown': STR, 'held_for_review': BOOL,
    }),
    'Answer': obj({
        'id': INT, 'question_id': INT, 'url': URL, 'api_url': URL, 'score': INT, 'accepted': BOOL,
        'author': nullable(ref('UserBrief')), 'created': DATETIME,
        'html': {'type': 'string', 'description': 'The rendered, sanitised HTML shown on the site.'},
        'markdown': {'type': 'string', 'description': 'The Markdown source.'},
        'held_for_review': BOOL,
        'versions': {'type': 'array', 'items': ref('PostVersion'),
                     'description': 'The versions it applies to (empty when none is given).'},
        'comments': {'type': 'array', 'items': ref('Comment')},
        'my_vote': nullable({'enum': ['up', 'down']}),
    }, required=['id', 'question_id', 'url', 'api_url', 'score', 'accepted', 'author', 'created', 'html', 'markdown',
                 'held_for_review', 'versions']),
    'Question': {
        'allOf': [ref('QuestionSummary'), obj({
            'html': STR, 'markdown': STR, 'deleted': BOOL,
            'versions': {'type': 'array', 'items': ref('PostVersion')},
            'comments': {'type': 'array', 'items': ref('Comment')},
            'answers': {'type': 'array', 'items': ref('Answer')},
            'my_vote': nullable({'enum': ['up', 'down']}),
            'following': BOOL,
        }, required=['html', 'markdown', 'deleted', 'versions', 'comments', 'answers'])],
        'description': 'A question with its answers (accepted first, then by score) and comments. my_vote and '
                       'following are given when a token is sent.',
    },
    'Tag': obj({
        'name': STR, 'url': URL, 'api_url': URL, 'count': {'type': 'integer', 'description': 'Questions with it.'},
        'description': {'type': 'string', 'description': 'Markdown.'},
        'synonyms': {'type': 'array', 'items': STR, 'description': 'Names that are stored as this tag.'},
        'created': nullable(DATETIME),
    }),
    'Badge': obj({
        'id': INT, 'name': STR, 'description': STR, 'type': {'enum': ['gold', 'silver', 'bronze']},
        'awarded_count': INT, 'url': URL,
    }),
    'Vote': obj({
        'post_id': INT, 'vote': nullable({'enum': ['up', 'down']}), 'score': INT,
        'votes_left': {'type': 'integer', 'description': 'Votes you can still cast in the next 24 hours.'},
    }),
    'Following': obj({'question_id': INT, 'following': BOOL}),
    'Index': obj({
        'name': STR, 'version': STR, 'docs': URL, 'openapi': URL,
        'links': {'type': 'object', 'additionalProperties': URL},
    }),
    'Problem': {
        'type': 'object',
        'description': 'An error (RFC 9457, application/problem+json).',
        'properties': {
            'type': URL, 'title': STR, 'status': INT, 'detail': STR, 'instance': STR,
            'errors': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': STR},
                       'description': 'Messages per field or parameter (validation-error, bad-request).'},
        },
        'required': ['type', 'title', 'status'],
    },
    'QuestionPage': page_of('QuestionSummary'),
    'AnswerList': page_of('Answer'),
    'TagPage': page_of('Tag'),
    'UserPage': page_of('User'),
    'BadgeList': page_of('Badge'),
    # Request bodies.
    'NewQuestion': obj({
        'title': STR, 'text': {'type': 'string', 'description': 'Markdown.'},
        'tags': {'anyOf': [{'type': 'array', 'items': STR}, STR],
                 'description': 'Tag names (a list, or one string separated by spaces).'},
        'wiki': {'type': 'boolean', 'description': 'Community wiki (when the site allows it).'},
    }, required=['title', 'text', 'tags']),
    'NewAnswer': obj({'text': {'type': 'string', 'description': 'Markdown.'},
                      'wiki': BOOL}, required=['text']),
    'NewComment': obj({'text': STR}),
    'VoteRequest': obj({'vote': {'enum': ['up', 'down', 'none'],
                                 'description': 'none takes your vote back.'}}),
}
