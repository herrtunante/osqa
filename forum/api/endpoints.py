"""The endpoints of /api/v1/. Each @endpoint is at once the URL pattern, the
handler and the OpenAPI operation (forum/api/core.py, openapi.py).

Reads show what the site shows the token's owner (or an anonymous visitor
without a token): Node.objects.visible_to() and review.can_view().
Writes go through the same Action classes, permission and reputation checks,
email confirmation rules, hourly posting limits and first posts review as
the web site (forum/views/writers.py, commands.py).
"""
import datetime

from django.db.models.functions import Coalesce
from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext as _, ngettext

from forum import settings
from forum.actions import (AcceptAnswerAction, AnswerAction, AskAction, CommentAction, VoteAction, VoteDownAction,
                           VoteUpAction)
from forum.forms import AnswerForm, AskForm
from forum.models import Answer, Badge, Node, Question, QuestionSubscription, Tag, TagSynonym, User
from forum.models.base import prefetch_cached_fk, prefetch_denormalized
from forum.modules import call_all_handlers
from forum.search.filters import STATUSES, SearchFilters
from forum.utils import accounts, post_versions, review
from forum.utils.antispam import posting_limit_message, posting_limit_reached
from forum.utils.similar import search_questions

from . import data as D
from .core import (Created, Key, ListResult, Param, Problem, absolute, api_url, choice_param, endpoint, int_param,
                   iso, json_body, page, parse_since, text_field)

LIMIT = Param('limit', 'query', {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30},
              'Items per page.')
CURSOR = Param('cursor', 'query', {'type': 'string'}, 'Where the page starts: take it from the next link of the '
               'previous page.')


def ID(what):
    return Param('id', 'path', {'type': 'integer'}, 'The %s id.' % what, example=1)


READ_ERRORS = ('bad-request', 'invalid-token', 'rate-limited')
WRITE_ERRORS = ('unauthorized', 'invalid-token', 'insufficient-scope', 'account-suspended', 'writes-disabled',
                'forbidden', 'rate-limited')


def _ip(request):
    return request.META.get('REMOTE_ADDR', '')


# ---- finding what the viewer may see ------------------------------------------

def visible_node(model, id, viewer):
    """The post `id` of `model` if `viewer` may see it (as on its page), else 404."""
    try:
        node = model.objects.get(id=id)
    except (model.DoesNotExist, ValueError):
        raise Http404()
    node = node.leaf
    if not isinstance(node, model) or node.node_type not in ('question', 'answer', 'comment'):
        raise Http404()
    for post in (node, node.abs_parent if node.abs_parent_id else None):
        if post is not None and post.nis.deleted and not viewer.can_view_deleted_post(post):
            raise Http404()
    if not review.can_view(node, viewer):
        raise Http404()
    return node


def fresh(node):
    """`node` read again after an action changed it (score, states)."""
    return type(node).objects.filter(id=node.id).first() or node


# ---- about ----------------------------------------------------------------------

@endpoint('GET', '/', operation_id='index', group='About', auth='none', response='Index',
          summary='API index', description='Links to the documentation, the OpenAPI document and the lists.')
def index(request):
    return {
        'name': str(settings.APP_SHORT_NAME),
        'version': 'v1',
        'docs': absolute(reverse('api_docs')),
        'openapi': api_url('/openapi.json'),
        'links': {name: api_url(path) for name, path in (
            ('questions', '/questions'), ('search', '/search'), ('tags', '/tags'), ('users', '/users'),
            ('badges', '/badges'), ('me', '/me'))},
    }


@endpoint('GET', '/openapi.json', operation_id='openapi', group='About', auth='none',
          response={'type': 'object', 'description': 'The OpenAPI 3.1 document.'},
          summary='OpenAPI document', description='This API described as OpenAPI 3.1, generated from the code.')
def openapi(request):
    from .openapi import document
    return document()


@endpoint('GET', '/me', operation_id='me', group='Members', auth='read', response='Me', etag=True,
          errors=('unauthorized', 'invalid-token', 'account-suspended'),
          summary="The token's owner", description='Who the token belongs to, its scopes and expiry.')
def me(request):
    token = request.api_token
    return {'user': D.user_data(request.user),
            'token': {'name': token.name, 'scopes': token.scope_list, 'expires': iso(token.expires)}}


# ---- questions ----------------------------------------------------------------

QUESTION_SORTS = {
    'active': [Key('activity', True, 'datetime'), Key('id')],
    'newest': [Key('added_at', True, 'datetime'), Key('id')],
    'votes': [Key('score', True, 'int'), Key('id')],
}


@endpoint('GET', '/questions', operation_id='listQuestions', group='Questions', response='QuestionPage',
          paginated=True, errors=READ_ERRORS, summary='List questions',
          description='Questions the viewer may see, newest activity first by default. Held posts are listed for '
                      'their author and moderators only, as on the site.',
          params=[
              Param('tags', 'query', {'type': 'string'}, 'Only questions with every one of these tags (names '
                    'separated by spaces or commas; synonyms work).', example='collect export'),
              Param('status', 'query', {'enum': list(STATUSES)}, 'unanswered, answered or accepted (has an '
                    'accepted answer).'),
              Param('sort', 'query', {'enum': list(QUESTION_SORTS), 'default': 'active'},
                    'active (latest activity), newest (asked) or votes (score).'),
              Param('since', 'query', {'type': 'string', 'format': 'date-time'}, 'Only questions with activity '
                    '(asked, answered, commented, edited) at or after this date or time (ISO 8601).',
                    example='2026-01-31'),
              LIMIT, CURSOR])
def list_questions(request):
    sort = choice_param(request, 'sort', list(QUESTION_SORTS), 'active')
    status = choice_param(request, 'status', STATUSES)
    questions = Question.objects.visible_to(request.user).annotate(activity=Coalesce('last_activity_at', 'added_at'))
    questions = SearchFilters(tags=request.GET.getlist('tags'), status=status).apply(questions)
    if request.GET.get('since'):
        questions = questions.filter(activity__gte=parse_since(request.GET['since']))
    rows, following = page(request, questions.prefetch_cached(fks=('author',), counts=('answer_count', 'accepted_count')),
                           QUESTION_SORTS[sort], sort)
    return ListResult([D.question_summary(q, request.user) for q in rows], following)


@endpoint('GET', '/questions/{id}', operation_id='getQuestion', group='Questions', response='Question', etag=True,
          params=[ID('question')], errors=('not-found', 'invalid-token', 'rate-limited'),
          summary='A question with its answers and comments',
          description='Rendered HTML and Markdown source of every post. Supports If-None-Match (ETag, 304).')
def get_question(request, id):
    return D.question_detail(visible_node(Question, id, request.user), request.user)


@endpoint('GET', '/questions/{id}/answers', operation_id='listAnswers', group='Answers', response='AnswerList',
          params=[ID('question')], errors=('not-found', 'invalid-token', 'rate-limited'),
          summary="A question's answers", description='Accepted first, then by score, each with its comments '
                                                      '(all of them: next is always null).')
def list_answers(request, id):
    question = visible_node(Question, id, request.user)
    answers = D.visible_answers(question, request.user)
    comments = D.visible_comments([a.id for a in answers], request.user)
    votes = D.my_votes(request.user, [a.id for a in answers])
    versions = post_versions.for_nodes([a.id for a in answers])
    return ListResult([D.answer_data(a, request.user, comments, votes, versions) for a in answers])


@endpoint('GET', '/answers/{id}', operation_id='getAnswer', group='Answers', response='Answer', etag=True,
          params=[ID('answer')], errors=('not-found', 'invalid-token', 'rate-limited'),
          summary='An answer with its comments', description='Supports If-None-Match (ETag, 304).')
def get_answer(request, id):
    answer = visible_node(Answer, id, request.user)
    votes = D.my_votes(request.user, [answer.id])
    return D.answer_data(answer, request.user, D.visible_comments([answer.id], request.user), votes)


@endpoint('GET', '/search', operation_id='search', group='Questions', response='QuestionPage',
          errors=READ_ERRORS, summary='Search questions',
          description='The site search (full text, best matches first) with the filters of the search page. '
                      'Returns at most limit results (next is always null). Only published questions are found.',
          params=[
              Param('q', 'query', {'type': 'string'}, 'What to look for. The search operators work: "a phrase", '
                    '-word, [tag], user:name, is:answered.', required=True, example='export csv'),
              Param('tags', 'query', {'type': 'string'}, 'Every one of these tags.'),
              Param('status', 'query', {'enum': list(STATUSES)}, 'unanswered, answered or accepted.'),
              Param('closed', 'query', {'enum': ['hide']}, 'hide leaves out closed questions.'),
              Param('date', 'query', {'enum': ['week', 'month', 'year']}, 'Asked in the last week, month or year.'),
              Param('from', 'query', {'type': 'string', 'format': 'date'}, 'Asked on or after (YYYY-MM-DD).'),
              Param('to', 'query', {'type': 'string', 'format': 'date'}, 'Asked on or before (YYYY-MM-DD).'),
              Param('author', 'query', {'type': 'string'}, 'Asked by this member (username).'),
              Param('code', 'query', {'enum': ['1']}, 'The question or an answer has a code block.'),
              Param('min_score', 'query', {'type': 'integer'}, 'A score of at least this.'),
              Param('version', 'query', {'type': 'string', 'maxLength': 20},
                    'The question or one of its answers applies to this version (of the given tags, if any).'),
              Param('limit', 'query', {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20},
                    'How many results.'),
          ])
def search(request):
    query = request.GET.get('q', '').strip()
    if not query:
        raise Problem('bad-request', _("q is required: what to look for."), errors={'q': [_("This field is required.")]})
    limit = int_param(request, 'limit', 20, 1, 50)
    params = request.GET.copy()
    if params.get('from') or params.get('to'):
        params.setdefault('date', 'custom')
    results = [q for q in search_questions(query[:200], limit, SearchFilters.from_params(params))
               if not q.nis.deleted and not review.is_held(q)]
    prefetch_cached_fk(results, 'author')
    prefetch_denormalized(results, 'answer_count', 'accepted_count')
    return ListResult([D.question_summary(q, request.user) for q in results])


# ---- tags, members, badges -------------------------------------------------------

TAG_SORTS = {'popular': [Key('used_count', True, 'int'), Key('id')],
             'name': [Key('name', False, 'str'), Key('id', False)]}


@endpoint('GET', '/tags', operation_id='listTags', group='Tags', response='TagPage', paginated=True,
          errors=READ_ERRORS, summary='List tags', description='Tags in use, with their synonyms.',
          params=[Param('q', 'query', {'type': 'string'}, 'Only tags whose name contains this.'),
                  Param('sort', 'query', {'enum': list(TAG_SORTS), 'default': 'popular'}, 'popular or name.'),
                  LIMIT, CURSOR])
def list_tags(request):
    sort = choice_param(request, 'sort', list(TAG_SORTS), 'popular')
    tags = Tag.active.all()
    if request.GET.get('q', '').strip():
        tags = tags.filter(name__icontains=request.GET['q'].strip().lower())
    rows, following = page(request, tags, TAG_SORTS[sort], sort)
    synonyms = D.synonyms_of(rows)
    return ListResult([D.tag_data(t, synonyms.get(t.id, [])) for t in rows], following)


@endpoint('GET', '/tags/{name}', operation_id='getTag', group='Tags', response='Tag', etag=True,
          params=[Param('name', 'path', {'type': 'string'}, 'The tag name (or one of its synonyms).',
                        example='collect')],
          errors=('not-found', 'invalid-token', 'rate-limited'), summary='A tag',
          description='A synonym gives the tag it stands for.')
def get_tag(request, name):
    name = name.strip().lower()
    tag = Tag.objects.filter(name=name).first()
    if tag is None:
        synonym = TagSynonym.objects.filter(source_name=name).select_related('target').first()
        tag = synonym.target if synonym is not None else None
    if tag is None:
        raise Http404()
    return D.tag_data(tag)


USER_SORTS = {'reputation': [Key('reputation', True, 'int'), Key('id')],
              'newest': [Key('date_joined', True, 'datetime'), Key('id')],
              'name': [Key('username', False, 'str'), Key('id', False)]}


@endpoint('GET', '/users', operation_id='listUsers', group='Members', response='UserPage', paginated=True,
          errors=READ_ERRORS, summary='List members', description='Public profile fields only.',
          params=[Param('q', 'query', {'type': 'string'}, 'Only members whose username contains this.'),
                  Param('sort', 'query', {'enum': list(USER_SORTS), 'default': 'reputation'},
                        'reputation, newest or name.'),
                  LIMIT, CURSOR])
def list_users(request):
    sort = choice_param(request, 'sort', list(USER_SORTS), 'reputation')
    users = User.objects.all()
    if request.GET.get('q', '').strip():
        users = users.filter(username__icontains=request.GET['q'].strip())
    rows, following = page(request, users, USER_SORTS[sort], sort)
    return ListResult([D.user_data(u) for u in rows], following)


@endpoint('GET', '/users/{id}', operation_id='getUser', group='Members', response='User', etag=True,
          params=[ID('member')], errors=('not-found', 'invalid-token', 'rate-limited'),
          summary="A member's public profile")
def get_user(request, id):
    try:
        return D.user_data(User.objects.get(id=id))
    except User.DoesNotExist:
        raise Http404()


@endpoint('GET', '/badges', operation_id='listBadges', group='Members', response='BadgeList', errors=READ_ERRORS,
          summary='List badges', description='Every badge, gold first (next is always null).')
def list_badges(request):
    from forum.badges.base import BadgesMeta
    BadgesMeta.install()
    badges = sorted(Badge.objects.filter(cls__in=list(BadgesMeta.by_class)), key=lambda b: (b.type, str(b.name)))
    return ListResult([D.badge_data(b) for b in badges])


# ---- writing ------------------------------------------------------------------

def _require_confirmed(user, item):
    if accounts.validation_required(user, item):
        raise Problem('email-confirmation-required', _("Confirm your email address first: open the link we emailed "
                                                       "you, or ask for a new one on the site."))


def _check_posting_limit(user, kind):
    if posting_limit_reached(user, kind):
        raise Problem('posting-limit', posting_limit_message(kind), headers={'Retry-After': '3600'})


def _check_form(form):
    # Members a CAPTCHA would be shown to (forum_modules/recaptcha, CAPTCHA_IF_REP_LESS_THAN) post on the site.
    if getattr(form, '_anti_spam_fields', None):
        raise Problem('captcha-required', _("Members with less than %d reputation points post on the web site, "
                                            "where they answer a CAPTCHA.") % int(settings.CAPTCHA_IF_REP_LESS_THAN))
    if not form.is_valid():
        errors = {name: [str(m) for m in messages] for name, messages in form.errors.items()}
        first = next(iter(errors.values()))[0] if errors else ''
        raise Problem('validation-error', first, errors=errors)
    return form.cleaned_data


def _check_spam(request, text, kind):
    """Spam checks of modules (forum_modules/akismet handlers.api_is_spam)."""
    if any(call_all_handlers('api_is_spam', request, text, kind)):
        raise Problem('spam', _("This looks like spam to our spam filter, so it wasn't posted."))


def _tags_text(value):
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return ' '.join(value)
    if isinstance(value, str):
        return value
    raise Problem('validation-error', _("tags must be a list of names or a string."),
                  errors={'tags': [_("Must be a list of names or a string.")]})


@endpoint('POST', '/questions', operation_id='askQuestion', group='Questions', auth='write', status=201,
          body='NewQuestion', response='Question',
          body_example={'title': 'How do I export survey data to CSV?',
                        'text': 'I need the data in **R**. Which menu exports it?', 'tags': ['collect', 'export']},
          errors=WRITE_ERRORS + ('email-confirmation-required', 'captcha-required', 'validation-error', 'spam',
                                 'posting-limit'),
          summary='Ask a question',
          description='As the ask page: the same length and tag rules, hourly limit and review of first posts '
                      '(held_for_review is true while a held post waits for a moderator).')
def ask_question(request):
    user = request.user
    payload = json_body(request)
    _require_confirmed(user, 'ask')
    _check_posting_limit(user, 'ask')
    form = AskForm({'title': text_field(payload, 'title', False), 'text': text_field(payload, 'text', False),
                    'tags': _tags_text(payload.get('tags', ''))}, user=user)
    cleaned = _check_form(form)
    _check_spam(request, cleaned['text'], 'forum-post')
    action = AskAction(user=user, ip=_ip(request)).save(data=cleaned)
    question = action.node
    if settings.WIKI_ON and payload.get('wiki') is True:
        question.nstate.wiki = action
    question = fresh(question)
    return Created(D.question_detail(question, user), api_url('/questions/%d' % question.id))


@endpoint('POST', '/questions/{id}/answers', operation_id='postAnswer', group='Answers', auth='write', status=201,
          body='NewAnswer', response='Answer', params=[ID('question')],
          body_example={'text': 'Use **File > Export > CSV** in the designer.'},
          errors=WRITE_ERRORS + ('not-found', 'conflict', 'email-confirmation-required', 'captcha-required',
                                 'validation-error', 'spam', 'posting-limit'),
          summary='Answer a question', description='Closed and deleted questions take no answers.')
def post_answer(request, id):
    user = request.user
    question = visible_node(Question, id, user)
    payload = json_body(request)
    if question.nis.closed or question.nis.deleted:
        raise Problem('conflict', _("This question is closed: it takes no new answers."))
    _require_confirmed(user, 'answer')
    _check_posting_limit(user, 'answer')
    cleaned = _check_form(AnswerForm({'text': text_field(payload, 'text', False)}, user))
    _check_spam(request, cleaned['text'], 'reply')
    action = AnswerAction(user=user, ip=_ip(request)).save(dict(question=question, **cleaned))
    answer = action.node
    if settings.WIKI_ON and payload.get('wiki') is True:
        answer.nstate.wiki = action
    answer = fresh(answer)
    return Created(D.answer_data(answer, user, {}, {}), api_url('/answers/%d' % answer.id))


@endpoint('POST', '/posts/{id}/comments', operation_id='postComment', group='Comments', auth='write', status=201,
          body='NewComment', response='Comment', params=[ID('question or answer')],
          body_example={'text': 'Which version do you use?'},
          errors=WRITE_ERRORS + ('not-found', 'email-confirmation-required', 'validation-error', 'spam',
                                 'posting-limit'),
          summary='Comment on a question or an answer',
          description='Needs the reputation to comment (except on your own posts and answers to your question).')
def post_comment(request, id):
    user = request.user
    post = visible_node(Node, id, user)
    if post.node_type not in ('question', 'answer'):
        raise Http404()
    payload = json_body(request)
    text = text_field(payload, 'text').strip()
    if len(text) < int(settings.FORM_MIN_COMMENT_BODY):
        raise Problem('validation-error', _("At least %d characters required on comment body.") %
                      int(settings.FORM_MIN_COMMENT_BODY), errors={'text': [_("Too short.")]})
    if len(text) > int(settings.FORM_MAX_COMMENT_BODY):
        raise Problem('validation-error', _("No more than %d characters on comment body.") %
                      int(settings.FORM_MAX_COMMENT_BODY), errors={'text': [_("Too long.")]})
    if not user.can_comment(post):
        _require_confirmed(user, 'comment')
        raise Problem('forbidden', _("You need %d reputation points to comment on other members' posts.") %
                      int(settings.REP_TO_COMMENT))
    _check_posting_limit(user, 'comment')
    _check_spam(request, text, 'comment')
    comment = CommentAction(user=user, ip=_ip(request)).save(data=dict(text=text, parent=post)).node
    comment = fresh(comment)
    return Created(D.comment_data(comment, user), absolute(comment.get_absolute_url()))


@endpoint('POST', '/posts/{id}/vote', operation_id='vote', group='Votes', auth='write', body='VoteRequest',
          response='Vote', params=[ID('question or answer')], body_example={'vote': 'up'},
          errors=WRITE_ERRORS + ('not-found', 'conflict', 'validation-error', 'posting-limit'),
          summary='Vote up, down, or take your vote back',
          description='Sets your vote to the given value (sending the same value twice changes nothing). The '
                      'reputation needed for each direction, the votes per day and the time after which a vote '
                      "can't be taken back are the site's.")
def vote(request, id):
    user = request.user
    post = visible_node(Node, id, user)
    if post.node_type not in ('question', 'answer'):
        raise Http404()
    payload = json_body(request)
    wanted = payload.get('vote')
    if wanted not in ('up', 'down', 'none'):
        raise Problem('validation-error', _("vote must be up, down or none."), errors={'vote': [_("Unknown value.")]})
    wanted = None if wanted == 'none' else wanted
    if user == post.author:
        raise Problem('forbidden', _("You can't vote on your own posts."))

    old = VoteAction.get_action_for(node=post, user=user)
    current = None if old is None else ('up' if old.action_type == 'voteup' else 'down')
    if wanted != current:
        if wanted == 'up' and not user.can_vote_up():
            raise Problem('forbidden', _("Voting up needs %(needed)d reputation points (you have %(have)d).") % {
                'needed': int(settings.REP_TO_VOTE_UP), 'have': user.reputation})
        if wanted == 'down' and not user.can_vote_down():
            raise Problem('forbidden', _("Voting down needs %(needed)d reputation points (you have %(have)d).") % {
                'needed': int(settings.REP_TO_VOTE_DOWN), 'have': user.reputation})
        if old is not None and old.action_date < datetime.datetime.now() - datetime.timedelta(
                days=int(settings.DENY_UNVOTE_DAYS)):
            raise Problem('conflict', ngettext("A vote can't be changed more than %d day after it was cast.",
                                               "A vote can't be changed more than %d days after it was cast.",
                                               int(settings.DENY_UNVOTE_DAYS)) % int(settings.DENY_UNVOTE_DAYS))
        if wanted is not None:
            cast_today = user.get_vote_count_today()
            if old is not None and old.action_date >= user._one_day_ago():
                cast_today -= 1  # replaced by the new one
            if cast_today >= user.can_vote_count_today():
                raise Problem('posting-limit', _("You have used your %d votes for today.") %
                              user.can_vote_count_today())
        if old is not None:
            old.cancel(ip=_ip(request))
        if wanted is not None:
            (VoteUpAction if wanted == 'up' else VoteDownAction)(user=user, node=post, ip=_ip(request)).save()
    return {'post_id': post.id, 'vote': wanted, 'score': fresh(post).score,
            'votes_left': max(0, user.can_vote_count_today() - user.get_vote_count_today())}


def _acceptable(request, id):
    user = request.user
    answer = visible_node(Answer, id, user)
    if settings.DISABLE_ACCEPTING_FEATURE:
        raise Problem('forbidden', _("Accepting answers is turned off on this site."))
    if not user.can_accept_answer(answer):
        raise Problem('forbidden', _("Only the asker (or a moderator) can accept an answer."))
    return answer


@endpoint('POST', '/answers/{id}/accept', operation_id='acceptAnswer', group='Answers', auth='write',
          response='Answer', params=[ID('answer')], errors=WRITE_ERRORS + ('not-found', 'conflict'),
          summary='Accept an answer', description="The site's limits on accepted answers per question apply. "
                                                  'Accepting an accepted answer changes nothing.')
def accept_answer(request, id):
    user = request.user
    answer = _acceptable(request, id)
    if not answer.nis.accepted:
        question = answer.question
        maximum = int(settings.MAXIMUM_ACCEPTED_ANSWERS or 0)
        if maximum and question.accepted_count >= maximum:
            raise Problem('conflict', ngettext("This question already has an accepted answer.",
                                               "This question has reached the limit of accepted answers.", maximum))
        per_user = int(settings.MAXIMUM_ACCEPTED_PER_USER or 0)
        if per_user and question.accepted_count and \
                question.accepted_answers.filter(author=answer.author).count() >= per_user:
            raise Problem('conflict', _("The author of this answer has reached the limit of accepted answers in "
                                        "this question."))
        AcceptAnswerAction(node=answer, user=user, ip=_ip(request)).save()
    answer = fresh(answer)
    return D.answer_data(answer, user, None, D.my_votes(user, [answer.id]))


@endpoint('DELETE', '/answers/{id}/accept', operation_id='unacceptAnswer', group='Answers', auth='write',
          response='Answer', params=[ID('answer')], errors=WRITE_ERRORS + ('not-found',),
          summary='Take an acceptance back')
def unaccept_answer(request, id):
    user = request.user
    answer = _acceptable(request, id)
    if answer.nis.accepted:
        answer.nstate.accepted.cancel(user, ip=_ip(request))
    answer = fresh(answer)
    return D.answer_data(answer, user, None, D.my_votes(user, [answer.id]))


def _follow(request, id, follow):
    user = request.user
    question = visible_node(Question, id, user)
    subscriptions = QuestionSubscription.objects.filter(question=question, user=user)
    if follow and not subscriptions.exists():
        QuestionSubscription(question=question, user=user, auto_subscription=False).save()
    elif follow:
        subscriptions.update(auto_subscription=False)  # followed on purpose now, as with the button
    elif not follow:
        subscriptions.delete()
    return {'question_id': question.id, 'following': follow}


@endpoint('POST', '/questions/{id}/follow', operation_id='followQuestion', group='Questions', auth='write',
          response='Following', params=[ID('question')], errors=WRITE_ERRORS + ('not-found',),
          summary='Follow a question', description='Get notified about its new answers and comments, as with '
                                                   'the Follow button (by email as set in your email settings).')
def follow_question(request, id):
    return _follow(request, id, True)


@endpoint('DELETE', '/questions/{id}/follow', operation_id='unfollowQuestion', group='Questions', auth='write',
          response='Following', params=[ID('question')], errors=WRITE_ERRORS + ('not-found',),
          summary='Stop following a question')
def unfollow_question(request, id):
    return _follow(request, id, False)

