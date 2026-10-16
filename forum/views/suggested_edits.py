"""Suggested edits (rules in forum/utils/suggested_edits.py).

- suggest_edit: the edit page of a question or answer for members who can't
  edit others' posts yet; saving it stores a SuggestedEdit for review.
- review_page: the "Review suggested edits" page for reviewers (moderators also
  see the suggestions in the moderation inbox, forum/views/moderation.py).
- review_action (POST): approve or reject, from either page.
- improve: the reviewer edits the suggestion further, then approves it.
- undo (POST): cancels the reviewer's last approval or rejection.
"""
from types import SimpleNamespace

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from forum.actions import ReviseAction
from forum.forms import EditAnswerForm, EditQuestionForm
from forum.http_responses import HttpResponseUnauthorized
from forum.models import Action, Node, SuggestedEdit
from forum.utils import review
from forum.utils import suggested_edits as sugg

PAGE_SIZE = 30
UNDO_SESSION_KEY = 'suggested_edit_undo'
UNDOABLE = ('suggestededit', 'rejectsuggestededit')


def _ip(request):
    return request.META.get('REMOTE_ADDR', '')


def _edit_template(post):
    return 'question_edit.html' if post.node_type == 'question' else 'answer_edit.html'


def _form_class(post):
    return EditQuestionForm if post.node_type == 'question' else EditAnswerForm


def _prepare(form, summary_required):
    """The edit form for a suggestion: no community wiki switch, and (for the
    suggester) a summary that is required."""
    form.fields.pop('wiki', None)
    if summary_required:
        field = form.fields['summary']
        field.required = True
        field.help_text = _("Say what you changed and why (e.g. fixed spelling, added the error message): "
                            "reviewers read it first.")
        # describe_fields() already made the bound field, which keeps its own copy of the help text.
        form._bound_fields_cache.pop('summary', None)
    return form


def _post_context(post, **context):
    context['question' if post.node_type == 'question' else 'answer'] = post
    return context


def suggest_edit(request, id):
    post = get_object_or_404(Node, id=id).leaf
    if post.node_type not in sugg.POST_TYPES:
        raise Http404
    if not request.user.is_authenticated:
        return HttpResponseRedirect('%s?next=%s' % (reverse('auth_signin'), request.path))
    if post.nis.deleted or not review.can_view(post, request.user):
        raise Http404
    if request.user.can_edit_post(post):
        return HttpResponseRedirect(reverse('edit_' + post.node_type, kwargs={'id': post.id}))
    if not sugg.can_suggest(request.user, post):
        return HttpResponseUnauthorized(request)
    if sugg.pending_for(post) is not None:
        messages.info(request, _("An edit to this post is already waiting for review. Please try again once it "
                                 "has been reviewed."))
        return HttpResponseRedirect(post.get_absolute_url())
    if sugg.limit_reached(request.user):
        messages.error(request, _("You have suggested as many edits as you can in a day. Please come back "
                                  "tomorrow."))
        return HttpResponseRedirect(post.get_absolute_url())

    form_class = _form_class(post)
    if request.method == 'POST':
        form = _prepare(form_class(post, request.user, data=request.POST), True)
        if form.is_valid():
            if not sugg.has_changes(post, form.cleaned_data):
                form.add_error(None, _("You haven't changed anything yet."))
            elif sugg.create(request.user, post, form.cleaned_data, _ip(request)) is None:
                messages.info(request, _("An edit to this post is already waiting for review."))
                return HttpResponseRedirect(post.get_absolute_url())
            else:
                messages.info(request, _("Thanks! Your edit will appear on the post once a reviewer has "
                                         "approved it."))
                return HttpResponseRedirect(post.get_absolute_url())
    else:
        form = _prepare(form_class(post, request.user), True)

    return render(request, _edit_template(post), _post_context(post, form=form, revision_form=None,
                                                                 suggesting=True))


def _next_url(request):
    target = request.POST.get('next') or ''
    if target.startswith(reverse('suggested_edits')) or target.startswith(reverse('admin_moderation')):
        return target
    return reverse('suggested_edits')


def _items(suggestions, viewer):
    items = []
    for suggestion in suggestions:
        post = suggestion.node.leaf
        merged = sugg.merged_values(suggestion)
        items.append({
            'suggestion': suggestion, 'post': post, 'question': post.absolute_parent,
            'diff': sugg.diff(suggestion), 'changed': sugg.changed_since(suggestion),
            'conflict': merged is None, 'can_review': sugg.can_review(viewer, suggestion),
        })
    return items


def edit_items(viewer, limit=PAGE_SIZE):
    """(cards, total) of the suggestions `viewer` may review (also for the moderation inbox)."""
    queue = sugg.queue_for(viewer)
    return _items(list(queue[:limit]), viewer), queue.count()


def review_page(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect('%s?next=%s' % (reverse('auth_signin'), request.path))
    if not sugg.is_reviewer(request.user):
        return HttpResponseUnauthorized(request)
    items, total = edit_items(request.user)
    undo = request.session.get(UNDO_SESSION_KEY)
    return render(request, 'suggested_edits/review.html', {
        'items': items, 'total': total, 'reasons': sugg.REJECT_REASONS,
        'undo': undo if isinstance(undo, dict) and undo.get('ids') else None,
        'next_url': reverse('suggested_edits'), 'enabled': sugg.enabled(),
    })


def _remember(request, action):
    if action is None:
        return
    request.session[UNDO_SESSION_KEY] = {'ids': [action.id]}
    if request.POST.get('next', '').startswith(reverse('admin_moderation')):
        from . import moderation
        moderation._remember(request, [action], action.action_type)


@require_POST
def review_action(request, id):
    suggestion = get_object_or_404(SuggestedEdit, id=id)
    if not sugg.can_review(request.user, suggestion):
        return HttpResponseUnauthorized(request)
    verb = request.POST.get('action')
    action = None
    if suggestion.status != SuggestedEdit.PENDING:
        messages.info(request, _("This suggested edit has already been reviewed."))
    elif verb == 'approve':
        action = sugg.approve(suggestion, request.user, _ip(request))
        if action is not None:
            messages.info(request, _("The edit is approved and credited to %s.") % suggestion.author.username)
        else:
            messages.info(request, _("The post was edited in the meantime in a way that conflicts with this "
                                     "suggestion, so it can't be applied any more."))
    elif verb == 'reject':
        reason = request.POST.get('reason', '')
        comment = request.POST.get('comment', '').strip()
        if reason not in sugg.REJECT_REASON_KEYS:
            messages.error(request, _("Please choose why you reject this edit."))
        elif reason == 'other' and not comment:
            messages.error(request, _("Please explain why you reject this edit."))
        else:
            action = sugg.reject(suggestion, request.user, reason, comment, _ip(request))
            if action is not None:
                messages.info(request, _("The edit is rejected; %s will be told why.") % suggestion.author.username)
    else:
        messages.error(request, _("Unknown action."))
    _remember(request, action)
    return HttpResponseRedirect(_next_url(request))


def improve(request, id):
    suggestion = get_object_or_404(SuggestedEdit, id=id)
    if not request.user.is_authenticated:
        return HttpResponseRedirect('%s?next=%s' % (reverse('auth_signin'), request.path))
    if not sugg.can_review(request.user, suggestion):
        return HttpResponseUnauthorized(request)
    if suggestion.status != SuggestedEdit.PENDING:
        messages.info(request, _("This suggested edit has already been reviewed."))
        return HttpResponseRedirect(reverse('suggested_edits'))
    merged = sugg.merged_values(suggestion)
    if merged is None:
        sugg.supersede(suggestion)
        messages.info(request, _("The post was edited in the meantime in a way that conflicts with this "
                                 "suggestion, so it can't be applied any more."))
        return HttpResponseRedirect(reverse('suggested_edits'))

    post = suggestion.node.leaf
    starting_point = SimpleNamespace(title=merged['title'], body=merged['body'], tagnames=merged['tagnames'])
    form_class = _form_class(post)
    if request.method == 'POST':
        form = _prepare(form_class(post, request.user, starting_point, data=request.POST), False)
        if form.is_valid():
            action = sugg.approve(suggestion, request.user, _ip(request))
            if action is None:
                messages.info(request, _("The post was edited in the meantime in a way that conflicts with this "
                                         "suggestion, so it can't be applied any more."))
                return HttpResponseRedirect(reverse('suggested_edits'))
            post = Node.objects.get(id=post.id).leaf
            data = dict(form.cleaned_data)
            if sugg.has_changes(post, data):
                data['summary'] = data.get('summary', '').strip() or _("Improved a suggested edit")
                ReviseAction(user=request.user, node=post, ip=_ip(request)).save(data=data)
            messages.info(request, _("The edit is approved and credited to %s, with your improvements.")
                          % suggestion.author.username)
            return HttpResponseRedirect(post.get_absolute_url())
    else:
        form = _prepare(form_class(post, request.user, starting_point), False)

    return render(request, _edit_template(post), _post_context(post, form=form, revision_form=None,
                                                                 improving=suggestion))


@require_POST
def undo(request):
    """Cancels the reviewer's last approval or rejection (the suggestion goes back to the queue)."""
    remembered = request.session.pop(UNDO_SESSION_KEY, None) or {}
    count = 0
    if request.user.is_authenticated:
        actions = Action.objects.filter(id__in=remembered.get('ids') or [], action_type__in=UNDOABLE, canceled=False)
        for action in actions:
            action = action.leaf
            reviewer_id = action.real_user_id if action.action_type == 'suggestededit' else action.user_id
            if reviewer_id == request.user.id:
                action.cancel(request.user, ip=_ip(request))
                count += 1
    if count:
        sugg.invalidate_counts()
        messages.info(request, _("Undone: the suggested edit is waiting for review again."))
    return HttpResponseRedirect(_next_url(request))
