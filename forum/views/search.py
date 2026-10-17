"""The site search page (/search/): full-text results with snippets, query
operators, filters kept in the URL (forum/search/filters.py) with chips and
facet counts, "did you mean", sort tabs and pagination. See forum/search/.

suggest (/search/suggest/?q=) feeds the header search box's suggestions."""
import hashlib
import json
import re
from urllib.parse import quote as urlquote, urlencode

from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, gettext as _, ngettext, pgettext
from django.views.decorators.http import require_GET

from forum import settings

from forum.feed import RssQuestionFeed
from forum.models import Node, Question, SearchDocument, Tag, TagSynonym, User
from forum.search import engine, spelling
from forum.search.filters import SearchFilters
from forum.search.query import parse, words
from forum.search.text import highlighter, html_to_text, mark, snippet, stem
from forum.utils import pagination
from forum.utils.review import hidden_q

from . import decorators

SORTS = ('relevance', 'newest', 'votes', 'active')
# Fewer results than this: look for a better spelling of the query.
FEW_RESULTS = 3


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


def search_url(q='', filters=None, sort='', **extra):
    """/search/ with the query text, the filters and the sort in its query string."""
    params = [('q', q)] if q else []
    if filters is not None:
        params += filters.params()
    if sort:
        params.append(('sort', sort))
    params += [(k, v) for k, v in extra.items() if v]
    return reverse('search') + ('?' + urlencode(params) if params else '')


def status_labels():
    return {'unanswered': _('Unanswered'), 'answered': _('Answered'), 'accepted': _('Accepted answer')}


def date_labels():
    return {'week': _('Past week'), 'month': _('Past month'), 'year': _('Past year'), 'custom': _('Custom range')}


def _date_chip(filters):
    if filters.date != 'custom':
        return date_labels()[filters.date]
    if filters.date_from and filters.date_to:
        return _('Asked %(start)s to %(end)s') % {'start': filters.date_from.isoformat(),
                                                  'end': filters.date_to.isoformat()}
    if filters.date_from:
        return _('Asked since %(date)s') % {'date': filters.date_from.isoformat()}
    return _('Asked until %(date)s') % {'date': filters.date_to.isoformat()}


def filter_chips(query, filters, sort):
    """The active filters, each with the URL of the search without it: the
    query's own operators first, then the filters of the form."""
    chips = []
    statuses = status_labels()
    for index, (kind, value, start, end) in enumerate(query.operators):
        if kind == 'tag':
            label = _('Tag: %(tag)s') % {'tag': value}
        elif kind == 'user':
            label = _('Asked by %(name)s') % {'name': value}
        else:
            label = statuses.get(value, value)
        chips.append({'label': label, 'url': search_url(query.without_operators(index), filters, sort)})
    q = query.raw
    for tag in filters.tags:
        chips.append({'label': _('Tag: %(tag)s') % {'tag': tag}, 'url': search_url(q, filters.without('tags', tag), sort)})
    for name, active, label in (
            ('status', filters.status, lambda: statuses[filters.status]),
            ('closed', filters.hide_closed, lambda: _('Closed questions hidden')),
            ('date', filters.date, lambda: _date_chip(filters)),
            ('author', filters.author, lambda: _('Asked by %(name)s') % {'name': filters.author}),
            ('code', filters.has_code, lambda: _('Has code')),
            ('min_score', filters.min_score is not None,
             lambda: _('Score at least %(score)d') % {'score': filters.min_score})):
        if active:
            chips.append({'label': label(), 'url': search_url(q, filters.without(name), sort)})
    return chips


def _facet_count(count, capped):
    return '%d+' % count if capped and count >= engine.FACET_CAP else str(count)


def search_facets(query, filters, questions, sort):
    """Counts for the status options and the commonest tags of the results,
    over at most engine.FACET_CAP matching questions."""
    rows = engine.matched_rows(questions)
    if filters.status or filters.hide_closed:
        base, _ranked = engine.search(query, filters=filters, skip=('status', 'closed'))
        base_rows = engine.matched_rows(base)
    else:
        base_rows = rows
    counts = engine.status_facets(base_rows)
    capped = counts['capped']
    statuses = status_labels()
    status_options = [{'value': '', 'label': _('Any'), 'count': _facet_count(counts['total'], capped),
                       'checked': not filters.status}]
    status_options += [{'value': value, 'label': statuses[value], 'count': _facet_count(counts[value], capped),
                        'checked': filters.status == value} for value in ('unanswered', 'answered', 'accepted')]
    leave_out = set(filters.tags) | set(query.tags)
    tags = [{'name': name, 'count': _facet_count(n, len(rows) >= engine.FACET_CAP),
             'url': search_url(query.raw, filters.copy(tags=filters.tags + [name]), sort)}
            for name, n in engine.tag_facets([row[0] for row in rows], leave_out=leave_out)]
    return {'status': status_options, 'closed': _facet_count(counts['closed'], capped), 'tags': tags}


def did_you_mean(query, filters, count, sort):
    """{'url', 'html'} of a better spelt query that finds more, or None."""
    if not (query.has_text or query.operators) or count >= FEW_RESULTS:
        return None
    corrected = spelling.suggest(query)
    if not corrected or corrected.strip().lower() == query.raw.strip().lower():
        return None
    questions, _ranked = engine.search(parse(corrected), filters=filters)
    if questions.count() <= count:
        return None
    typed = set(w.lower() for w in query.raw.split())
    html = ' '.join(('<strong>%s</strong>' if w.lower() not in typed else '%s') % escape(w) for w in corrected.split())
    return {'url': search_url(corrected, filters, sort), 'html': mark_safe(html), 'text': corrected}


def _page_details(questions, query):
    """For each question on the page: the marked title, a snippet and its status."""
    ids = [q.id for q in questions]
    documents = SearchDocument.objects.in_bulk(ids)
    regex = highlighter(query.highlight_terms())
    results = []
    for question in questions:
        document = documents.get(question.id)
        if document is not None:
            text = snippet(regex, document.body, document.answers)
        else:
            text = snippet(regex, html_to_text(question.body))
        answers, accepted = question.answer_count, bool(question.accepted_count)
        if question.nis.closed:
            status = ('closed', pgettext('search result status', 'Closed'))
        elif accepted:
            status = ('accepted', pgettext('search result status', 'Accepted'))
        elif answers:
            status = ('answered', pgettext('search result status', 'Answered'))
        else:
            status = ('unanswered', pgettext('search result status', 'Unanswered'))
        results.append({'question': question, 'title': mark(question.headline, regex), 'snippet': text,
                        'answers': answers, 'accepted': accepted, 'status': status[0], 'status_label': status[1]})
    return results


def result_count_text(count):
    if count == 1:
        return _('1 result')
    return ngettext('About %(count)s result', 'About %(count)s results', count) % {'count': intcomma(count)}


@decorators.render('search.html', 'questions')
def search_results(request, keywords):
    query = parse(keywords)
    filters = SearchFilters.from_params(request.GET)
    sort = request.GET.get('sort') if request.GET.get('sort') in SORTS else ''
    context = {'keywords': keywords, 'query': query, 'filters': filters, 'sort': sort, 'results': [],
               'searching': False, 'page_title': _('Search'), 'date_labels': date_labels(),
               'status_options': [{'value': '', 'label': _('Any'), 'checked': not filters.status}] + [
                   {'value': value, 'label': label, 'checked': filters.status == value}
                   for value, label in status_labels().items()]}
    if query.is_empty and filters.is_empty:
        return context

    questions, ranked = engine.search(query, filters=filters)
    if request.GET.get('type') == 'rss':
        questions = questions.order_by('search_rank') if ranked else questions.order_by('-added_at')
        title = _("questions matching '%(keywords)s'") % {'keywords': keywords or search_url('', filters)}
        return RssQuestionFeed(request, questions, title, title)(request)

    plain_text = query.without_operators()
    chips = filter_chips(query, filters, sort)
    context.update({
        'searching': True,
        'questions': questions.prefetch_cached(fks=('author',), counts=('answer_count', 'accepted_count')),
        'ranked': ranked,
        'page_title': _("Search results for %(query)s") % {'query': keywords} if keywords else _('Search results'),
        'feed_url': search_url(keywords, filters, type='rss'),
        'ask_url': '%s?%s' % (reverse('ask'), urlencode({'title': query.title_text})) if query.title_text
        else reverse('ask'),
        'chips': chips,
        'clear_all_url': search_url(plain_text, None, sort) if chips else '',
        'plain_text': plain_text,
    })
    context['facets'] = search_facets(query, filters, questions, sort)
    context['status_options'] = context['facets']['status']
    context = pagination.paginated(request, ('questions', SearchPaginatorContext(ranked)), context)
    paginator = context['questions'].paginator
    context['results'] = _page_details(list(paginator.page), query)
    context['result_count'] = paginator.count
    context['result_count_text'] = result_count_text(paginator.count) if paginator.count else _('No results')
    context['did_you_mean'] = did_you_mean(query, filters, paginator.count, sort)
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
        questions = engine.apply_filters(Question.objects.public(), query)
        for match in (Q(title__istartswith=partial), Q(title__icontains=' ' + partial)):
            if len(found) < limit:
                found += list(questions.filter(match).exclude(id__in=[q.id for q in found]).order_by(
                    '-score', '-last_activity_at')[:limit - len(found)])

    counts = {row['parent_id']: row for row in Node.objects.filter(
        parent_id__in=[q.id for q in found], node_type='answer').exclude(
        hidden_q()).values('parent_id').annotate(
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
    limit = SUGGEST_LIMITS['tags']
    tags = [(tag, None) for tag in _starting_with_first(Tag.active.all(), 'name', word, ('-used_count', 'name'), limit)]
    if len(tags) < limit:
        # A synonym typed ("csv") suggests its tag ("export, also called csv").
        listed = [tag.id for tag, _synonym in tags]
        for synonym in TagSynonym.objects.filter(source_name__istartswith=word, target__used_count__gte=1).exclude(
                target_id__in=listed).select_related('target').order_by('source_name')[:limit - len(tags)]:
            if synonym.target_id not in listed:
                listed.append(synonym.target_id)
                tags.append((synonym.target, synonym.source_name))
    results = []
    for tag, synonym in tags:
        meta = ngettext('%(count)d question', '%(count)d questions', tag.used_count) % {'count': tag.used_count}
        if synonym:
            meta = '%s · %s' % (_('also called %(synonym)s') % {'synonym': synonym}, meta)
        results.append({'name': tag.name, 'url': tag.get_absolute_url(), 'count': tag.used_count, 'meta': meta})
    return results


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
