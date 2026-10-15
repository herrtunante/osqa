"""The site search page (/search/): full-text results with snippets, query
operators, sort tabs and pagination. See forum/search/.

suggest (/search/suggest/?q=) feeds the header search box's suggestions."""
import hashlib
import json
import re
from urllib.parse import quote as urlquote, urlencode

from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import get_language, gettext as _, ngettext
from django.views.decorators.http import require_GET

from forum import settings

from forum.feed import RssQuestionFeed
from forum.models import Node, Question, SearchDocument, Tag, User
from forum.search import engine
from forum.search.query import parse, words
from forum.search.text import highlighter, html_to_text, snippet, stem
from forum.utils import pagination

from . import decorators


class SearchPaginatorContext(pagination.PaginatorContext):
    def __init__(self, ranked):
        sorts = [
            ('newest', pagination.SimpleSort(_('newest'), ('-added_at', '-id'), _("most recently asked questions"))),
            ('votes', pagination.SimpleSort(_('votes'), ('-score', '-added_at'), _("most voted questions"))),
            ('active', pagination.SimpleSort(_('active'), ('-last_activity_at', '-id'),
                                             _("most recently updated questions"))),
        ]
        if ranked:
            sorts.insert(0, ('relevance', pagination.SimpleSort(_('relevance'), ('search_rank', '-added_at'),
                                                                _("most relevant questions"))))
        super().__init__('SEARCH_RESULTS', sort_methods=sorts, pagesizes=(15, 30, 50), default_pagesize=30)


def search(request):
    keywords = (request.GET.get('q') or '').strip()
    search_type = request.GET.get('t')
    if keywords and search_type == 'tag':
        return HttpResponseRedirect(reverse('tags') + '?q=%s' % urlquote(keywords))
    if keywords and search_type == 'user':
        return HttpResponseRedirect(reverse('users') + '?q=%s' % urlquote(keywords))
    return search_results(request, keywords)


def _page_details(questions, query):
    """For each question on the page: answer counts and a snippet."""
    ids = [q.id for q in questions]
    counts = {row['parent_id']: row for row in Node.objects.filter(parent_id__in=ids, node_type='answer').exclude(
        state_string__contains='(deleted)').values('parent_id').annotate(
        answers=Count('id'), accepted=Count('id', filter=Q(marked=True)))}
    documents = SearchDocument.objects.in_bulk(ids)
    regex = highlighter(query.highlight_terms())
    results = []
    for question in questions:
        document = documents.get(question.id)
        if document is not None:
            text = snippet(regex, document.body, document.answers)
        else:
            text = snippet(regex, html_to_text(question.body))
        row = counts.get(question.id, {})
        results.append({'question': question, 'snippet': text,
                        'answers': row.get('answers', 0), 'accepted': bool(row.get('accepted'))})
    return results


@decorators.render('search.html', 'questions')
def search_results(request, keywords):
    query = parse(keywords)
    context = {'keywords': keywords, 'query': query, 'results': [], 'page_title': _('Search')}
    if query.is_empty:
        return context

    questions, ranked = engine.search(query)
    if request.GET.get('type') == 'rss':
        questions = questions.order_by('search_rank') if ranked else questions.order_by('-added_at')
        title = _("questions matching '%(keywords)s'") % {'keywords': keywords}
        return RssQuestionFeed(request, questions, title, title)(request)

    context.update({
        'questions': questions,
        'ranked': ranked,
        'page_title': _("Search results for %(query)s") % {'query': keywords},
        'feed_url': '%s?%s' % (reverse('search'), urlencode({'q': keywords, 'type': 'rss'})),
        'ask_url': '%s?%s' % (reverse('ask'), urlencode({'title': query.title_text})) if query.title_text
        else reverse('ask'),
        'without_filters_url': '%s?%s' % (reverse('search'), urlencode({'q': query.title_text}))
        if query.has_filters and query.title_text else '',
    })
    context = pagination.paginated(request, ('questions', SearchPaginatorContext(ranked)), context)
    context['results'] = _page_details(list(context['questions'].paginator.page), query)
    return context


# -- Header search box suggestions ------------------------------------------

SUGGEST_MIN_LENGTH = 2
SUGGEST_MAX_LENGTH = 100
SUGGEST_LIMITS = {'questions': 5, 'tags': 4, 'users': 3}
SUGGEST_CACHE_SECONDS = 60


def _last_token(text):
    """(kind, word) for the word being typed: kind is 'tag' ([name, tag:name),
    'user' (user:name, @name), 'any' (a plain word) or None (another operator,
    an exclusion, a quoted phrase, or a word already followed by a space)."""
    if not text or text[-1].isspace():
        return None, ''
    token = text.split()[-1]
    lower = token.lower()
    if lower.startswith('tag:') or token.startswith('['):
        return 'tag', token.split(':', 1)[1] if ':' in token else token[1:].rstrip(']')
    if lower.startswith('user:') or token.startswith('@'):
        return 'user', token.split(':', 1)[1].strip('"') if ':' in token else token[1:]
    if ':' in token or token.startswith(('-', '"')):
        return None, ''
    return 'any', token


def _starting_with_first(queryset, field, word, order, limit):
    """Rows whose `field` contains `word`, those starting with it first."""
    matching = queryset.filter(**{field + '__icontains': word})
    first = list(matching.filter(**{field + '__istartswith': word}).order_by(*order)[:limit])
    if len(first) < limit:
        first += list(matching.exclude(id__in=[row.id for row in first]).order_by(*order)[:limit - len(first)])
    return first


def _suggested_questions(query, kind, word):
    limit = SUGGEST_LIMITS['questions']
    found = []
    if not query.is_empty:
        questions, ranked = engine.search(query, limit=limit * 4)
        found = list(questions.order_by('search_rank' if ranked else '-last_activity_at')[:limit])
    if len(found) < limit and kind == 'any' and len(word) >= SUGGEST_MIN_LENGTH and words(word):
        # The word being typed is usually incomplete ("rast"): titles with a
        # word starting with it.
        # Titles starting with it first.
        partial = words(word)[-1]
        questions = engine.apply_filters(Question.objects.filter_state(deleted=False), query)
        for match in (Q(title__istartswith=partial), Q(title__icontains=' ' + partial)):
            if len(found) < limit:
                found += list(questions.filter(match).exclude(id__in=[q.id for q in found]).order_by(
                    '-score', '-last_activity_at')[:limit - len(found)])

    counts = {row['parent_id']: row for row in Node.objects.filter(
        parent_id__in=[q.id for q in found], node_type='answer').exclude(
        state_string__contains='(deleted)').values('parent_id').annotate(
        answers=Count('id'), accepted=Count('id', filter=Q(marked=True)))}
    results = []
    for question in found:
        row = counts.get(question.id, {})
        answers = row.get('answers', 0)
        results.append({'title': question.headline, 'url': question.get_absolute_url(),
                        'meta': ngettext('%(count)d answer', '%(count)d answers', answers) % {'count': answers},
                        'accepted': bool(row.get('accepted'))})
    return results


def _suggested_tags(kind, word):
    if kind not in ('any', 'tag') or len(word) < SUGGEST_MIN_LENGTH:
        return []
    tags = _starting_with_first(Tag.active.all(), 'name', word, ('-used_count', 'name'), SUGGEST_LIMITS['tags'])
    return [{'name': tag.name, 'url': tag.get_absolute_url(), 'count': tag.used_count,
             'meta': ngettext('%(count)d question', '%(count)d questions', tag.used_count) % {
                 'count': tag.used_count}}
            for tag in tags]


def _avatar_url(user, size=64):
    return 'https://secure.gravatar.com/avatar/%s?%s' % (user.gravatar, urlencode({
        's': size, 'd': str(settings.GRAVATAR_DEFAULT_IMAGE), 'r': str(settings.GRAVATAR_ALLOWED_RATING)}))


def _suggested_users(kind, word):
    if kind not in ('any', 'user') or len(word) < SUGGEST_MIN_LENGTH:
        return []
    users = _starting_with_first(User.objects.filter(is_active=True), 'username', word, ('-reputation', 'id'),
                                 SUGGEST_LIMITS['users'])
    # What the users page shows of each member: name, picture, reputation.
    return [{'name': user.username, 'url': user.get_profile_url(), 'avatar': _avatar_url(user),
             'reputation': user.reputation,
             'meta': ngettext('%(count)d reputation point', '%(count)d reputation points', user.reputation) % {
                 'count': user.reputation}}
            for user in users]


def suggestions(text):
    """Questions, tags and people matching what is typed in the search box."""
    query = parse(text)
    kind, word = _last_token(text)
    word = word.strip('[]"@')
    highlight = [stem(t) for t in query.terms] + [w for p in query.phrases for w in words(p)]
    if kind in ('tag', 'user') and words(word):
        highlight.append(words(word)[0])
    return {
        'q': text,
        'search_url': '%s?%s' % (reverse('search'), urlencode({'q': text})),
        'highlight': list(dict.fromkeys(w for w in highlight if w)),
        'questions': _suggested_questions(query, kind, word),
        'tags': _suggested_tags(kind, word),
        'users': _suggested_users(kind, word),
    }


def _json(data):
    # Escaped so that the body can never be read as HTML, even if sniffed.
    content = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace(
        '&', '\\u0026')
    return HttpResponse(content, content_type='application/json')


@require_GET
def suggest(request):
    """JSON for the header search box: a few questions (the search engine's
    best matches, never deleted ones), tags and people. The same for every
    visitor, so each query's answer is cached for a minute."""
    # Spaces normalised; a trailing one is kept (the last word is complete).
    text = re.sub(r'\s+', ' ', request.GET.get('q') or '').lstrip()[:SUGGEST_MAX_LENGTH]
    if len(text.strip()) < SUGGEST_MIN_LENGTH:
        data = {'q': text, 'search_url': '', 'highlight': [], 'questions': [], 'tags': [], 'users': []}
    else:
        key = 'search-suggest:' + hashlib.sha256(('%s\n%s' % (get_language(), text.lower())).encode()).hexdigest()
        data = cache.get(key)
        if data is None:
            data = suggestions(text)
            cache.set(key, data, SUGGEST_CACHE_SECONDS)
    response = _json(data)
    response['Cache-Control'] = 'max-age=%d' % SUGGEST_CACHE_SECONDS
    return response
