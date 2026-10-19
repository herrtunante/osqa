""""Questions for you" (/questions/for-you/): open questions without an
accepted answer in the member's best tags, the ones they were asked to answer
(forum/utils/expertise.py) first, and "Not for me" on each of them."""
from django.contrib import messages
from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from forum.models import Answer, AnswerRequest, Question
from forum.modules import decorate
from forum.utils import expertise, pagination, review
from forum.views.commands import AnonymousNotAllowedException
from forum.views.decorators import command, render


class ForYouPaginatorContext(pagination.PaginatorContext):
    def __init__(self):
        super().__init__('FOR_YOU_LIST', pagesizes=(15, 30, 50), default_pagesize=30)


def for_you_questions(user, tag_ids):
    """The questions of the page, the pending requests first, newest first."""
    pending = AnswerRequest.objects.filter(user_id=user.id, state=AnswerRequest.PENDING)
    # Answered or declined: done with them.
    done = AnswerRequest.objects.filter(user_id=user.id).exclude(state=AnswerRequest.PENDING).values('question_id')
    live_answers = Answer.objects.filter_state(deleted=False)
    matching = Q(id__in=pending.values('question_id'))
    if tag_ids:
        matching |= Q(id__in=Question.objects.filter(tags__id__in=tag_ids).values('id'))
    return Question.objects.public().filter(matching).exclude(author_id=user.id) \
        .exclude(state_string__contains='(closed)') \
        .exclude(id__in=live_answers.filter(marked=True).values('parent_id')) \
        .exclude(id__in=live_answers.filter(author_id=user.id).values('parent_id')) \
        .exclude(id__in=done) \
        .annotate(requested=Exists(pending.filter(question_id=OuterRef('pk')))) \
        .order_by('-requested', '-added_at', '-id')


@render('questions_for_you.html', 'for_you', tabbed=False)
def questions_for_you(request):
    user = request.user
    if not user.is_authenticated:
        return HttpResponseRedirect('%s?next=%s' % (reverse('auth_signin'), request.path))
    top = expertise.top_tags(user)
    questions = for_you_questions(user, [row.tag_id for row in top])
    context = pagination.paginated(request, ('questions', ForYouPaginatorContext()), {
        'questions': questions.prefetch_cached(fks=('last_activity_by',), counts=('answer_count', 'accepted_count')),
        'top_tags': top,
        'suggestions_on': expertise.requests_enabled(user),
        'page_title': _("Questions for you"),
        'preferences_url': reverse('user_preferences', kwargs={'id': user.id, 'slug': slugify(user.username)}),
    })
    # Why each asked question is here ("you've answered 4 questions tagged collect").
    page = list(context['questions'].paginator.page)
    asked = [q.id for q in page if q.requested]
    reasons = {r.question_id: r for r in AnswerRequest.objects.filter(user_id=user.id, question_id__in=asked)} \
        if asked else {}
    for question in page:
        question.answer_request = reasons.get(question.id)
    context['questions'].paginator.page = page
    return context


@decorate.withfn(command)
def decline_answer_request(request, id):
    """POST: "Not for me". The question is never suggested to the member again
    and leaves their "Questions for you" page."""
    if not request.user.is_authenticated:
        raise AnonymousNotAllowedException(_('decline questions'))
    question = get_object_or_404(Question, id=id)
    if not review.can_view(question, request.user) or question.nis.deleted:
        raise Http404()
    expertise.decline(request.user, question)
    messages.info(request, _("Got it: we won't suggest that question to you again."))
    target = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = reverse('questions_for_you')
    return HttpResponseRedirect(target)
