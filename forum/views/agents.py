"""Forum content for AI agents and other programs.

- /questions/<id>.md: one thread as Markdown with metadata
- /llms.txt: what the site is and how to read it (https://llmstxt.org/)
- /export/qa.jsonl: all answered questions, one JSON object per line
- /mcp: a read-only Model Context Protocol server (Streamable HTTP transport,
  JSON responses) with the tools search_forum and get_question

Only public content is exposed (not deleted, not held for review), always with its URL, so agents
can cite the thread it came from.
"""
import json

from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt

from forum import settings
from forum.models import Question, Answer, Comment, AISuggestion
from forum.utils import post_versions, review
from forum.utils.similar import search_questions

MCP_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
MAX_SEARCH_RESULTS = 20


def absolute(url):
    return url if '://' in url else '%s%s' % (settings.APP_BASE_URL, url)


def answered_questions():
    answered = Answer.objects.filter(parent=OuterRef('pk')).public()
    return Question.objects.public().filter(Exists(answered))


def public_question(id):
    try:
        question = Question.objects.get(id=id)
    except (Question.DoesNotExist, ValueError):
        raise Http404()
    if question.nis.deleted or review.is_held(question):
        raise Http404()
    return question


def source(node):
    """A post's Markdown source (the stored body is rendered HTML)."""
    revision = node.active_revision
    return (revision.body if revision else strip_tags(node.body)).strip()


def visible_answers(question):
    # active_revision holds the Markdown source; authors come from the object cache.
    answers = list(Answer.objects.filter(parent=question).public()
                   .select_related('active_revision').prefetch_cached(fks=('author',))
                   .order_by('-score', 'added_at'))
    return sorted(answers, key=lambda a: not a.nis.accepted)


def thread_comments(question):
    """{post id: [comments]} for the question and its answers, in one query."""
    comments = {}
    for comment in (Comment.objects.filter(Q(parent=question) | Q(parent__parent=question))
                    .public().select_related('active_revision')
                    .prefetch_cached(fks=('author',)).order_by('added_at')):
        comments.setdefault(comment.parent_id, []).append(comment)
    return comments


def question_status(question, answers):
    if question.duplicate_of:
        return 'duplicate'
    if question.nis.closed:
        return 'closed'
    if any(a.nis.accepted for a in answers):
        return 'accepted'
    return 'answered' if answers else 'unanswered'


def question_data(question, with_text=True, answers=None):
    """The thread as plain data (used by the JSON export and the MCP tools)."""
    if answers is None:
        answers = visible_answers(question)
    data = {
        'id': question.id,
        'url': absolute(question.get_absolute_url()),
        'title': question.title,
        'tags': question.tagname_list(),
        'score': question.score,
        'status': question_status(question, answers),
        'asked': question.added_at.isoformat(),
        'last_activity': (question.last_activity_at or question.added_at).isoformat(),
        'answer_count': len(answers),
    }
    if question.duplicate_of:
        data['duplicate_of'] = absolute(question.duplicate_of.get_absolute_url())
    if with_text:
        data['question'] = source(question)
        data['answers'] = [{
            'url': absolute(answer.get_absolute_url()),
            'accepted': bool(answer.nis.accepted),
            'score': answer.score,
            'author': answer.author.username,
            'date': answer.added_at.isoformat(),
            'text': source(answer),
        } for answer in answers]
    return data


def _versions_md(versions):
    """The line under a post saying which versions it applies to (forum/utils/post_versions.py)."""
    parts = []
    for pv in versions:
        text = pv.version.label
        if pv.confirmed is not None:
            text += ', confirmed for %s' % pv.confirmed.name
            if pv.confirmed_at:
                text += ' on %s' % pv.confirmed_at.strftime('%Y-%m-%d')
        if pv.outdated:
            text += ' (the current version is %s; this may be outdated)' % pv.current.name
        parts.append(text)
    return ['Applies to: %s' % '; '.join(parts), ''] if parts else []


def _comments_md(comments):
    return ['> %s (%s)' % (' '.join(source(c).split()), c.author.username) for c in comments]


def question_markdown(question):
    answers = visible_answers(question)
    comments = thread_comments(question)
    versions = post_versions.for_nodes([question.id] + [a.id for a in answers], question.tagname_list())
    meta = question_data(question, with_text=False, answers=answers)
    lines = ['---']
    for key in ('title', 'url', 'tags', 'status', 'score', 'answer_count', 'asked', 'last_activity', 'duplicate_of'):
        if key in meta:
            lines.append('%s: %s' % (key, json.dumps(meta[key], ensure_ascii=False)))
    lines.append('source: %s' % json.dumps(str(settings.APP_TITLE), ensure_ascii=False))
    if str(settings.APP_COPYRIGHT).strip():
        lines.append('license: %s' % json.dumps(str(settings.APP_COPYRIGHT), ensure_ascii=False))
    lines += ['---', '', '# %s' % question.title, '',
              'Asked by %s on %s' % (question.author.username, question.added_at.strftime('%Y-%m-%d')), '']
    lines += _versions_md(versions.get(question.id, ()))
    lines += [source(question), '']
    lines += _comments_md(comments.get(question.id, ()))
    for answer in answers:
        heading = 'Accepted answer' if answer.nis.accepted else 'Answer'
        lines += ['', '## %s (score %d, by %s on %s)' % (heading, answer.score, answer.author.username,
                                                        answer.added_at.strftime('%Y-%m-%d')),
                  '', 'Link: %s' % absolute(answer.get_absolute_url()), '']
        lines += _versions_md(versions.get(answer.id, ()))
        lines += [source(answer), '']
        lines += _comments_md(comments.get(answer.id, ()))
    if not answers:
        lines += ['', '_No answers yet._']
    return '\n'.join(lines).rstrip() + '\n'


def question_md(request, id):
    question = public_question(id)
    response = HttpResponse(question_markdown(question), content_type='text/markdown; charset=utf-8')
    response['Link'] = '<%s>; rel="canonical"' % absolute(question.get_absolute_url())
    return response


def llms_txt(request):
    base = settings.APP_BASE_URL
    top = answered_questions().order_by('-score', '-added_at')[:30]
    lines = [
        '# %s' % settings.APP_TITLE, '',
        '> %s' % ' '.join(str(settings.APP_DESCRIPTION).split()), '',
        'A question and answer forum. Every thread is available as Markdown by adding `.md` to '
        '/questions/<id> (for example %s/questions/%d.md). Accepted answers are marked; prefer them '
        'and higher-scored answers, check the dates, and link to the thread you used.' % (base, top[0].id if top else 1),
        '',
        '## Machine-readable access', '',
        '- [All answered questions as JSON Lines](%s/export/qa.jsonl): one thread per line, with answers' % base,
        '- [MCP server](%s/mcp): Model Context Protocol (Streamable HTTP) with the tools search_forum and get_question' % base,
        '- [Search](%s/search/?q=export+csv): the search page; it takes the operators "a phrase", -word, [tag], '
        'user:name, is:answered/is:unanswered/is:accepted and the filters tags=, status=unanswered|answered|accepted, '
        'closed=hide, date=week|month|year (or from=/to= YYYY-MM-DD), author=, code=1, min_score=, version=, '
        'sort=relevance|newest|votes|active (the MCP tool search_forum takes query, tags, status, since and version)'
        % base,
        '- [JSON API](%s/api/): read questions, answers, tags and members (and post with a token); OpenAPI 3.1 at '
        '%s/api/v1/openapi.json' % (base, base),
        '- [Sitemap](%s/sitemap.xml)' % base,
        '- [Latest questions (RSS)](%s/feeds/rss/)' % base,
        '',
        '## Top questions', '',
    ]
    lines += ['- [%s](%s/questions/%d.md)' % (q.title.replace(']', ')'), base, q.id) for q in top]
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')


def qa_export(request):
    questions = answered_questions().order_by('id')

    def lines():
        for question in questions.iterator():
            yield json.dumps(question_data(question), ensure_ascii=False) + '\n'

    response = StreamingHttpResponse(lines(), content_type='application/x-ndjson; charset=utf-8')
    response['Content-Disposition'] = 'inline; filename="qa.jsonl"'
    return response


# ---- MCP ---------------------------------------------------------------

MCP_TOOLS = [
    {
        'name': 'search_forum',
        'title': 'Search the forum',
        'description': 'Find questions on the %(site)s Q&A forum that match a query. Returns titles, URLs, '
                       'status (accepted / answered / unanswered) and scores; use get_question for the full thread.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'What to look for, in plain words.'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_SEARCH_RESULTS, 'default': 10},
                'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5,
                         'description': 'Only questions with every one of these tags.'},
                'status': {'type': 'string', 'enum': ['unanswered', 'answered', 'accepted'],
                           'description': 'Only questions without answers, with answers, or with an accepted answer.'},
                'since': {'type': 'string', 'format': 'date',
                          'description': 'Only questions asked on or after this date (YYYY-MM-DD).'},
                'version': {'type': 'string', 'maxLength': 20,
                            'description': 'Only questions whose question or an answer applies to this version of '
                                           'the software (e.g. 4.1; of the given tags, if any).'},
            },
            'required': ['query'],
        },
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'get_question',
        'title': 'Read a forum thread',
        'description': 'The full thread of a question on the %(site)s forum as Markdown: the question, its answers '
                       '(accepted first, then by score) and comments, with links to cite.',
        'inputSchema': {
            'type': 'object',
            'properties': {'id': {'type': 'integer', 'description': 'The question id, from search_forum or its URL.'}},
            'required': ['id'],
        },
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
]


class ToolError(Exception):
    pass


def tool_filters(arguments):
    """The search filters of search_forum's arguments (the search page's, forum/search/filters.py)."""
    from forum.search.filters import STATUSES, SearchFilters, parse_date
    tags = arguments.get('tags') or []
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ToolError('tags must be a list of tag names')
    status = arguments.get('status') or ''
    if status and status not in STATUSES:
        raise ToolError('status must be one of %s' % ', '.join(STATUSES))
    since = None
    if arguments.get('since'):
        since = parse_date(arguments['since'])
        if since is None:
            raise ToolError('since must be a date (YYYY-MM-DD)')
    return SearchFilters(tags=tags, status=status, date='custom' if since else '', date_from=since,
                         version=str(arguments.get('version') or ''))


def call_tool(name, arguments):
    if name == 'search_forum':
        query = str(arguments.get('query', '')).strip()
        if not query:
            raise ToolError('query is required')
        try:
            limit = max(1, min(int(arguments.get('limit', 10)), MAX_SEARCH_RESULTS))
        except (TypeError, ValueError):
            limit = 10
        results = [question_data(q, with_text=False) for q in search_questions(query, limit, tool_filters(arguments))]
        return json.dumps({'results': results}, ensure_ascii=False, indent=1)
    if name == 'get_question':
        try:
            return question_markdown(public_question(int(arguments.get('id'))))
        except (Http404, TypeError, ValueError):
            raise ToolError('no such question')
    raise ToolError('unknown tool %s' % name)


def mcp_result(request_id, result):
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def mcp_error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


def handle_mcp_message(message):
    """The JSON-RPC response to one message, or None for a notification."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or 'method' not in message:
        return mcp_error(message.get('id') if isinstance(message, dict) else None, -32600, 'Invalid Request')
    method, params, request_id = message['method'], message.get('params') or {}, message.get('id')
    if 'id' not in message:
        return None  # notifications (e.g. notifications/initialized) need no answer

    if method == 'initialize':
        requested = params.get('protocolVersion')
        return mcp_result(request_id, {
            'protocolVersion': requested if requested in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0],
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'osqa-forum', 'title': str(settings.APP_SHORT_NAME), 'version': '1.0'},
            'instructions': 'Read-only access to the %s Q&A forum (%s). Search first, then read threads; prefer '
                            'accepted and higher-scored answers, mind their dates, and cite the thread URL.'
                            % (settings.APP_SHORT_NAME, ' '.join(str(settings.APP_DESCRIPTION).split())),
        })
    if method == 'ping':
        return mcp_result(request_id, {})
    if method == 'tools/list':
        site = {'site': settings.APP_SHORT_NAME}
        tools = [dict(tool, description=tool['description'] % site) for tool in MCP_TOOLS]
        return mcp_result(request_id, {'tools': tools})
    if method == 'tools/call':
        try:
            text = call_tool(params.get('name'), params.get('arguments') or {})
        except ToolError as e:
            return mcp_result(request_id, {'content': [{'type': 'text', 'text': str(e)}], 'isError': True})
        return mcp_result(request_id, {'content': [{'type': 'text', 'text': text}], 'isError': False})
    return mcp_error(request_id, -32601, 'Method not found: %s' % method)


@csrf_exempt
def mcp(request):
    if request.method == 'GET':
        # No server-initiated stream: this server only answers requests.
        return HttpResponse(status=405, headers={'Allow': 'POST'})
    if request.method != 'POST':
        return HttpResponse(status=405, headers={'Allow': 'POST'})
    try:
        payload = json.loads(request.body or b'null')
    except ValueError:
        return JsonResponse(mcp_error(None, -32700, 'Parse error'), status=400)

    if isinstance(payload, list):
        responses = [r for r in (handle_mcp_message(m) for m in payload) if r is not None]
        return JsonResponse(responses, safe=False) if responses else HttpResponse(status=202)
    response = handle_mcp_message(payload)
    return JsonResponse(response) if response is not None else HttpResponse(status=202)


# ---- AI-suggested answers ---------------------------------------------

def ai_suggestion_feedback(request, id, verdict):
    """The asker or a moderator marks an AI suggestion helpful, or removes it."""
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseNotAllowed, HttpResponseRedirect
    from forum.http_responses import HttpResponseUnauthorized

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    suggestion = get_object_or_404(AISuggestion, id=id)
    question = suggestion.question.leaf
    user = request.user
    if not user.is_authenticated or not (user == question.author or user.is_staff or user.is_superuser):
        return HttpResponseUnauthorized(request)
    if verdict == 'helpful':
        suggestion.resolve(user, AISuggestion.HELPFUL)
    elif verdict == 'remove':
        suggestion.resolve(user, AISuggestion.REMOVED)
    return HttpResponseRedirect(question.get_absolute_url())
