"""POST /posts/<id>/confirm-version/: "Still works in <current version>" on an
answer written for an older version, or (with `undo`) taking that back. The
rules are in forum/utils/post_versions.py; the confirmation is a
ConfirmVersionAction (forum/actions/version.py)."""
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext as _

from forum.models import Action, Answer
from forum.utils import post_versions, review


def confirm_version(request, id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    answer = get_object_or_404(Answer, id=id)
    question = answer.question
    if not review.can_view(answer, request.user) or question is None or not review.can_view(question, request.user):
        raise Http404
    if (answer.nis.deleted or question.nis.deleted) and not request.user.can_view_deleted_post(answer):
        raise Http404
    if not request.user.is_authenticated:
        return HttpResponseRedirect(reverse('auth_signin'))

    raw = request.POST.get('tag', '')
    tag_id = int(raw) if raw.isdigit() else None
    found = [pv for pv in post_versions.for_nodes([answer.id]).get(answer.id, []) if pv.tag_id == tag_id]
    post_version = found[0] if found else None
    if post_version is None:
        raise Http404
    ip = request.META.get('REMOTE_ADDR', '')

    if request.POST.get('undo'):
        if not post_versions.can_undo(request.user, post_version):
            return HttpResponseForbidden()
        action = Action.objects.filter(id=post_version.confirmed_action_id, canceled=False).first()
        if action is not None:
            action.leaf.cancel(request.user, ip)
            messages.info(request, _("The confirmation was taken back."))
        return HttpResponseRedirect(answer.get_absolute_url())

    if not post_versions.can_confirm(request.user, answer, post_version):
        return HttpResponseForbidden()
    post_versions.confirm(request.user, answer, tag_id, ip)
    messages.info(request, _("Thanks! The answer is now marked as working in %(version)s.") % {
        'version': escape(post_version.current.label)})
    return HttpResponseRedirect(answer.get_absolute_url())
