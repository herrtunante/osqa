# encoding:utf-8
import logging
import os.path
import re

import datetime

from django.urls import reverse
from forum.utils.urls import root_relative_reverse
from django.core.files.storage import FileSystemStorage
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect, HttpResponse, HttpResponseNotAllowed, Http404, JsonResponse
from django.utils.html import *
from django.utils.translation import gettext as _

from django.contrib import messages
from forum.utils.antispam import posting_limit_reached, posting_limit_message

from forum.actions import AskAction, AnswerAction, ReviseAction, RollbackAction, RetagAction, AnswerToQuestionAction, CommentToQuestionAction
from forum.forms import *
from forum.models import *
from forum.models.node import render_markdown
from forum.utils import html
from forum.http_responses import HttpResponseUnauthorized

from .vars import PENDING_SUBMISSION_SESSION_ATTR

IMAGE_SIGNATURES = (
    b'\x89PNG\r\n\x1a\n',       # PNG
    b'\xff\xd8\xff',              # JPEG
    b'GIF87a', b'GIF89a',
    b'BM',                         # BMP
    b'II*\x00', b'MM\x00*',        # TIFF
    b'\x00\x00\x01\x00',          # ICO
)


def looks_like_image(f):
    head = f.read(16)
    f.seek(0)
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return True
    return any(head.startswith(signature) for signature in IMAGE_SIGNATURES)


# Longer than any post body the forms accept; the preview refuses anything bigger.
MAX_PREVIEW_LENGTH = 100000


def markdown_preview(request):
    """Renders editor Markdown exactly as a saved post would be rendered."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    text = request.POST.get('text', '')
    if len(text) > MAX_PREVIEW_LENGTH:
        return HttpResponse(status=413)
    return HttpResponse(render_markdown(text) if text.strip() else '', content_type='text/html; charset=utf-8')


def upload(request):
    """Image upload from the editor (image dialog, paste and drop).

    Answers JSON: {"url": "/upfiles/name.png"} on success, otherwise
    {"error": "message for the user"} with status 403 (not allowed), 400
    (not an image), 413 (too big) or 500.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    def refused(message, status):
        return JsonResponse({'error': message}, status=status)

    if not request.user.can_upload_files():
        return refused(_('uploading images is limited to users with >60 reputation points'), 403)

    f = request.FILES.get('file-upload')
    file_types = _("allowed file types are 'jpg', 'jpeg', 'gif', 'bmp', 'png', 'tiff', 'webp'")
    if f is None:
        return refused(file_types, 400)

    if os.path.splitext(f.name)[1].lower() not in ('.jpg', '.jpeg', '.gif', '.png', '.bmp', '.tiff', '.ico', '.webp'):
        return refused(file_types, 400)

    # check file size (in bytes) before writing anything to disk
    if f.size > float(settings.ALLOW_MAX_FILE_SIZE) * 1024 * 1024:
        return refused(_("maximum upload file size is %sM") % settings.ALLOW_MAX_FILE_SIZE, 413)

    # The extension is chosen by the client; check the content too.
    if not looks_like_image(f):
        return refused(file_types, 400)

    try:
        storage = FileSystemStorage(str(settings.UPFILES_FOLDER), str(settings.UPFILES_ALIAS))
        new_file_name = storage.save("_".join(f.name.split()), f)
    except Exception:
        logging.exception('File upload failed')
        return refused(_('Error uploading file. Please contact the site administrator. Thank you.'), 500)

    return JsonResponse({'url': str(settings.UPFILES_ALIAS) + new_file_name})

def ask(request):
    form = None

    if request.POST:
        if request.session.pop('reviewing_pending_data', False):
            form = AskForm(initial=request.POST, user=request.user)
        elif "text" in request.POST:
            form = AskForm(request.POST, user=request.user)
            if form.is_valid():
                if request.user.is_authenticated and posting_limit_reached(request.user, 'ask'):
                    messages.error(request, posting_limit_message('ask'))
                elif request.user.is_authenticated and request.user.email_valid_and_can_ask():
                    ask_action = AskAction(user=request.user, ip=request.META['REMOTE_ADDR']).save(data=form.cleaned_data)
                    question = ask_action.node

                    if settings.WIKI_ON and request.POST.get('wiki', False):
                        question.nstate.wiki = ask_action

                    return HttpResponseRedirect(question.get_absolute_url())
                else:
                    request.session[PENDING_SUBMISSION_SESSION_ATTR] = {
                        'POST': request.POST.urlencode(),
                        'data_name': _("question"),
                        'type': 'ask',
                        'submission_url': reverse('ask'),
                        'time': datetime.datetime.now().isoformat()
                    }

                    if request.user.is_authenticated:
                        messages.info(request, _("Your question is pending until you %s.") % html.hyperlink(
                            settings.APP_URL + root_relative_reverse('send_validation_email'), _("validate your email")

                        ))
                        return HttpResponseRedirect(reverse('index'))
                    else:
                        return HttpResponseRedirect(reverse('auth_signin'))
        elif "go" in request.POST:
            form = AskForm({'title': request.POST['q']}, user=request.user)
            
    if not form:
        form = AskForm(user=request.user)

    return render(request, 'ask.html', {
        'form' : form,
        'tab' : 'ask'
        })

def convert_to_question(request, id):
    user = request.user

    node_type = request.GET.get('node_type', 'answer')
    if node_type == 'comment':
        node = get_object_or_404(Comment, id=id)
        action_class = CommentToQuestionAction
    else:
        node = get_object_or_404(Answer, id=id)
        action_class = AnswerToQuestionAction

    if not user.can_convert_to_question(node):
        return HttpResponseUnauthorized(request)

    return _edit_question(request, node, template='node/convert_to_question.html', summary=_("Converted to question"),
                           action_class =action_class, allow_rollback=False, url_getter=lambda a: Question.objects.get(id=a.id).get_absolute_url())

def edit_question(request, id):
    question = get_object_or_404(Question, id=id)
    if question.nis.deleted and not request.user.can_view_deleted_post(question):
        raise Http404
    if request.user.can_edit_post(question):
        return _edit_question(request, question)
    elif request.user.can_retag_questions():
        return _retag_question(request, question)
    else:
        raise Http404

def _retag_question(request, question):
    if request.method == 'POST':
        form = RetagQuestionForm(question, request.POST)
        if form.is_valid():
            if form.has_changed():
                RetagAction(user=request.user, node=question, ip=request.META['REMOTE_ADDR']).save(data=dict(tagnames=form.cleaned_data['tags']))

            return HttpResponseRedirect(question.get_absolute_url())
    else:
        form = RetagQuestionForm(question)
    return render(request, 'question_retag.html', {
        'question': question,
        'form' : form,
        #'tags' : _get_tags_cache_json(),
    })

def _edit_question(request, question, template='question_edit.html', summary='', action_class=ReviseAction,
                   allow_rollback=True, url_getter=lambda q: q.get_absolute_url(), additional_context=None):
    if request.method == 'POST':
        revision_form = RevisionForm(question, data=request.POST)
        if revision_form.is_valid():
            revision = question.revisions.get(revision=revision_form.cleaned_data['revision'])
        else:
            revision = question.active_revision

        if 'select_revision' in request.POST:
            form = EditQuestionForm(question, request.user, revision)
        else:
            form = EditQuestionForm(question, request.user, revision, data=request.POST)

        if not 'select_revision' in request.POST and form.is_valid():
            if form.has_changed():
                action = action_class(user=request.user, node=question, ip=request.META['REMOTE_ADDR']).save(data=form.cleaned_data)

                if settings.WIKI_ON:
                    if request.POST.get('wiki', False) and not question.nis.wiki:
                        question.nstate.wiki = action
                    elif question.nis.wiki and (not request.POST.get('wiki', False)) and request.user.can_cancel_wiki(question):
                        question.nstate.wiki = None
            else:
                if not revision == question.active_revision:
                    if allow_rollback:
                        RollbackAction(user=request.user, node=question).save(data=dict(activate=revision))
                    else:
                        pass

            return HttpResponseRedirect(url_getter(question))
    else:
        revision_form = RevisionForm(question)
        form = EditQuestionForm(question, request.user, initial={'summary': summary})

    context = {
        'question': question,
        'revision_form': revision_form,
        'form' : form,
    }

    if not (additional_context is None):
        context.update(additional_context)

    return render(request, template, context)


def edit_answer(request, id):
    answer = get_object_or_404(Answer, id=id)
    if answer.deleted and not request.user.can_view_deleted_post(answer):
        raise Http404
    elif not request.user.can_edit_post(answer):
        raise Http404

    if request.method == "POST":
        revision_form = RevisionForm(answer, data=request.POST)
        if revision_form.is_valid():
            revision = answer.revisions.get(revision=revision_form.cleaned_data['revision'])
        else:
            revision = answer.active_revision

        if 'select_revision' in request.POST:
            form = EditAnswerForm(answer, request.user, revision)
        else:
            form = EditAnswerForm(answer, request.user, revision, data=request.POST)

        if not 'select_revision' in request.POST and form.is_valid():
            if form.has_changed():
                action = ReviseAction(user=request.user, node=answer, ip=request.META['REMOTE_ADDR']).save(data=form.cleaned_data)

                if settings.WIKI_ON:
                    if request.POST.get('wiki', False) and not answer.nis.wiki:
                        answer.nstate.wiki = action
                    elif answer.nis.wiki and (not request.POST.get('wiki', False)) and request.user.can_cancel_wiki(answer):
                        answer.nstate.wiki = None
            else:
                if not revision == answer.active_revision:
                    RollbackAction(user=request.user, node=answer, ip=request.META['REMOTE_ADDR']).save(data=dict(activate=revision))

            return HttpResponseRedirect(answer.get_absolute_url())

    else:
        revision_form = RevisionForm(answer)
        form = EditAnswerForm(answer, request.user)
    return render(request, 'answer_edit.html', {
                              'answer': answer,
                              'revision_form': revision_form,
                              'form': form,
                              })

def answer(request, id):
    question = get_object_or_404(Question, id=id)

    if request.POST:
        form = AnswerForm(request.POST, request.user)

        if request.session.pop('reviewing_pending_data', False) or not form.is_valid():
            request.session['redirect_POST_data'] = request.POST.urlencode()
            return HttpResponseRedirect(question.get_absolute_url() + '#fmanswer')

        if request.user.is_authenticated and posting_limit_reached(request.user, 'answer'):
            messages.error(request, posting_limit_message('answer'))
            request.session['redirect_POST_data'] = request.POST.urlencode()
            return HttpResponseRedirect(question.get_absolute_url() + '#fmanswer')

        if request.user.is_authenticated and request.user.email_valid_and_can_answer():
            answer_action = AnswerAction(user=request.user, ip=request.META['REMOTE_ADDR']).save(dict(question=question, **form.cleaned_data))
            answer = answer_action.node

            if settings.WIKI_ON and request.POST.get('wiki', False):
                answer.nstate.wiki = answer_action

            return HttpResponseRedirect(answer.get_absolute_url())
        else:
            request.session[PENDING_SUBMISSION_SESSION_ATTR] = {
                'POST': request.POST.urlencode(),
                'data_name': _("answer"),
                'type': 'answer',
                'submission_url': reverse('answer', kwargs={'id': id}),
                'time': datetime.datetime.now().isoformat()
            }

            if request.user.is_authenticated:
                messages.info(request, _("Your answer is pending until you %s.") % html.hyperlink(
                    settings.APP_URL + root_relative_reverse('send_validation_email'), _("validate your email")
                ))
                return HttpResponseRedirect(question.get_absolute_url())
            else:
                return HttpResponseRedirect(reverse('auth_signin'))

    return HttpResponseRedirect(question.get_absolute_url())


def manage_pending_data(request, action, forward=None):
    pending_data = request.session.pop(PENDING_SUBMISSION_SESSION_ATTR, None)

    if not pending_data:
        raise Http404

    if action == _("cancel"):
        return HttpResponseRedirect(forward or request.META.get('HTTP_REFERER', '/'))
    else:
        if action == _("review"):
            request.session['reviewing_pending_data'] = True

        request.session['redirect_POST_data'] = pending_data['POST']
        return HttpResponseRedirect(pending_data['submission_url'])




TAG_NAME_RE = re.compile(r'^[\w+#.-]+$', re.UNICODE)


def tag_edit(request, tag):
    """Moderators edit a tag's description and its synonyms."""
    from forum.models import Tag, TagSynonym, MarkedTag
    from forum.actions import RetagAction

    if not (request.user.is_authenticated and (request.user.is_superuser or request.user.is_staff)):
        raise Http404
    tag = get_object_or_404(Tag, name=tag)
    error = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'description':
            tag.description = request.POST.get('description', '').strip()[:2000]
            tag.save()
            messages.info(request, _("The tag description was saved."))
            return HttpResponseRedirect(tag.get_absolute_url())

        elif action == 'add_synonym':
            name = request.POST.get('synonym', '').strip().lower()
            if not name or not TAG_NAME_RE.match(name) or len(name) > int(settings.FORM_MAX_LENGTH_OF_TAG):
                error = _("Enter a valid tag name.")
            elif name == tag.name:
                error = _("A tag cannot be a synonym of itself.")
            elif TagSynonym.objects.filter(source_name=name).exists():
                error = _("%s is already a synonym.") % name
            elif TagSynonym.objects.filter(source_name=tag.name).exists():
                error = _("%s is itself a synonym of another tag.") % tag.name
            else:
                merge_tag(request, Tag.objects.filter(name=name).first(), tag)
                TagSynonym(source_name=name, target=tag, created_by=request.user).save()
                messages.info(request, _("%(synonym)s is now a synonym of %(tag)s.") % {'synonym': name, 'tag': tag.name})
                return HttpResponseRedirect(reverse('tag_edit', kwargs={'tag': tag.name}))

        elif action == 'remove_synonym':
            TagSynonym.objects.filter(id=request.POST.get('id'), target=tag).delete()
            return HttpResponseRedirect(reverse('tag_edit', kwargs={'tag': tag.name}))

    return render(request, 'tag_edit.html', {
        'tag': tag,
        'synonyms': tag.synonyms.all(),
        'error': error,
    })


def merge_tag(request, source, target):
    """Move the questions, synonyms and user selections of tag `source` to `target`."""
    from forum.models import TagSynonym, MarkedTag
    from forum.actions import RetagAction

    if source is None or source.id == target.id:
        return

    for question in Question.objects.filter(tags__id=source.id).distinct():
        names = []
        for name in question.tagname_list():
            name = target.name if name == source.name else name
            if name not in names:
                names.append(name)
        RetagAction(user=request.user, node=question, ip=request.META['REMOTE_ADDR']).save(
            data=dict(tagnames=' '.join(names)))

    TagSynonym.objects.filter(target=source).update(target=target)
    for mark in MarkedTag.objects.filter(tag=source):
        if MarkedTag.objects.filter(tag=target, user=mark.user).exists():
            mark.delete()
        else:
            mark.tag = target
            mark.save()
