"""GET /questions/<id>/updates/: what changed on a question page while it is
open (media/js/osqa.live.js). See forum/utils/live.py for the snapshot, the
`since` token and the two-part ETag."""
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed, HttpResponseNotModified, JsonResponse
from django.template import engines
from django.template.loader import render_to_string
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.translation import ngettext

from forum import settings
from forum.models import Answer, Comment, Question
from forum.utils import live, review


def _finish(response, etag=None):
    if etag:
        response['ETag'] = etag
    patch_cache_control(response, private=True, no_cache=True)
    patch_vary_headers(response, ('Cookie', 'Accept-Language'))
    response['X-Robots-Tag'] = 'noindex'
    return response


def _not_modified(etag):
    return _finish(HttpResponseNotModified(), etag)


def question_updates(request, id):
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    since = request.GET.get('since', '')
    since_at = live.parse_token(since)
    if since and since_at is None:
        return _finish(JsonResponse({'error': 'since'}, status=400))
    posts = request.GET.get('posts')

    # Nothing changed anywhere on the site since the browser's copy: no query at all.
    stamp = live.stamp(request.user, since) if posts is None else None
    browser_stamp, browser_hash = live.split_etag(request.META.get('HTTP_IF_NONE_MATCH'))
    if stamp is not None and browser_stamp == stamp:
        return _not_modified(live.make_etag(stamp, browser_hash))

    try:
        question = Question.objects.get(id=id)
    except Question.DoesNotExist:
        raise Http404()
    if question.nis.deleted and not request.user.can_view_deleted_post(question):
        raise Http404()
    if not review.can_view(question, request.user):
        raise Http404()

    if posts is not None:
        return _finish(JsonResponse(_rendered_posts(request, question, posts)))

    data = live.snapshot(question, request.user, since_at)
    digest = live.snapshot_hash(data)
    etag = live.make_etag(stamp, digest)
    if browser_hash == digest:
        return _not_modified(etag)
    data['poll'] = live.POLL_SECONDS
    return _finish(JsonResponse(data), etag)


def _ids(value):
    ids = []
    for part in value.split(','):
        part = part.strip()
        if part.isdigit() and int(part) not in ids:
            ids.append(int(part))
    return ids[:live.MAX_POSTS]


def _rendered_posts(request, question, value):
    """The posts `value` (comma-separated ids) of `question` the visitor may see,
    rendered as on the question page."""
    from forum.templatetags.node_tags import comment_context
    user = request.user
    ids = _ids(value)
    result = {'posts': [], 'question': None}

    answers = list(Answer.objects.filter(id__in=ids, parent_id=question.id).visible_to(user)
                   .prefetch_cached(fks=('author', 'parent')).order_by('id'))
    for answer in answers:
        result['posts'].append({'id': answer.id, 'type': 'answer', 'parent': question.id,
                                'rev': answer.active_revision_id, 'html': render_to_string(
                                    'node/answer.html', {'answer': answer, 'focused_answer_id': None}, request)})

    shown = {question.id} | {answer.id for answer in answers}
    comments = Comment.objects.filter(id__in=ids).filter(Q(parent_id=question.id) | Q(parent__parent_id=question.id)) \
        .visible_to(user).order_by('id')
    for comment in comments:
        parent = comment.parent
        if parent.id not in shown:
            if parent.nis.deleted or not review.can_view(parent, user):
                continue
            shown.add(parent.id)
        result['posts'].append({'id': comment.id, 'type': 'comment', 'parent': parent.id,
                                'rev': comment.active_revision_id, 'html': render_to_string(
                                    'node/comment.html', {'comment': comment_context(comment, user, top_scorer=True),
                                                          'show_gravatar': settings.FORM_GRAVATAR_IN_COMMENTS}, request)})

    if question.id in ids:
        engine = engines['django']
        result['question'] = {
            'id': question.id, 'rev': question.active_revision_id, 'title': question.headline,
            'body': question.html,
            'tags': render_to_string('node/question_tags.html', {'question': question}, request),
            'info': engine.from_string('{% load node_tags %}{% contributors_info question %}').render(
                {'question': question}, request),
        }

    count = Answer.objects.filter(parent_id=question.id).visible_to(user).count()
    result['answer_count'] = count
    result['answers_heading'] = ngettext('%(counter)s Answer', '%(counter)s Answers', count) % {'counter': count}
    return result
