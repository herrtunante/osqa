"""Tag management for moderators: the per-tag management page (description,
versions, synonyms, rename, merge, history with undo), the merge confirmation
page and the list of all synonyms. The work and its rules are in
forum/utils/tags.py."""
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from forum import settings
from forum.models import Action, Tag, TagSynonym, TagVersion
from forum.utils import tags as tagtools
from forum.utils.tags import NeedsMerge, TagError
from forum.views.admin import TOOL_LINKS

# Listed with the admin tools (the admin menu is data: forum/views/admin.py).
TOOL_LINKS.append(('tag_synonyms', _lazy('Tag synonyms')))


def _require_moderator(request):
    if not tagtools.can_manage_tags(request.user):
        raise Http404


def _ip(request):
    return request.META.get('REMOTE_ADDR', '')


def merge_url(source, target):
    return '%s?%s' % (reverse('tag_merge', kwargs={'tag': source.name}), urlencode({'into': target.name}))


def _manage_page(request, tag, error=None, **values):
    from forum.models import Question
    questions = Question.objects.filter(tags__id=tag.id).distinct().count()
    from forum.models import NodeVersion
    from django.db.models import Count
    versions = list(TagVersion.objects.filter(tag=tag).order_by('position', 'id'))
    used = dict(NodeVersion.objects.filter(tag=tag).values_list('version_id').annotate(n=Count('id')))
    for version in versions:
        version.used = used.get(version.id, 0)
    return render(request, 'tag_edit.html', {
        'tag': tag,
        'versions': versions,
        'versions_enabled': bool(settings.POST_VERSIONS),
        'synonyms': tag.synonyms.all(),
        'questions_count': questions,
        'history': tagtools.history(tag),
        'error': error,
        'values': values,
    })


def tag_edit(request, tag):
    """The management page of a tag (from its page: "Manage tag")."""
    _require_moderator(request)
    synonym = TagSynonym.objects.filter(source_name=tag).select_related('target').first()
    if synonym and not Tag.objects.filter(name=tag).exists():
        return HttpResponseRedirect(reverse('tag_edit', kwargs={'tag': synonym.target.name}))
    tag = get_object_or_404(Tag, name=tag)

    if request.method != 'POST':
        return _manage_page(request, tag)

    action, user, ip = request.POST.get('action'), request.user, _ip(request)
    edit_url = reverse('tag_edit', kwargs={'tag': tag.name})
    try:
        if action == 'description':
            tagtools.edit_description(tag, request.POST.get('description', ''), user, ip)
            messages.info(request, _("The tag description was saved."))
            return HttpResponseRedirect(tag.get_absolute_url())

        if action in VERSION_ACTIONS:
            return _version_change(request, tag, action, edit_url)

        if action == 'add_synonym':
            try:
                name = tagtools.add_synonym(tag, request.POST.get('synonym', ''), user, ip).extra['synonym']
            except NeedsMerge as e:
                return HttpResponseRedirect(merge_url(e.source, e.target))
            messages.info(request, _("%(synonym)s is now a synonym of %(tag)s.") % {'synonym': name, 'tag': tag.name})
            return HttpResponseRedirect(edit_url)

        if action == 'remove_synonym':
            synonym = TagSynonym.objects.filter(id=request.POST.get('id'), target=tag).first()
            if synonym:
                tagtools.remove_synonym(synonym, user, ip)
                messages.info(request, _("%s is no longer a synonym.") % synonym.source_name)
            return HttpResponseRedirect(edit_url)

        if action == 'rename':
            old = tag.name
            done = tagtools.rename_tag(tag, request.POST.get('name', ''), user, ip,
                                       keep_synonym=bool(request.POST.get('keep_synonym')))
            messages.info(request, _("The tag %(old)s is now called %(new)s.") % {'old': old, 'new': done.extra['new']})
            return HttpResponseRedirect(reverse('tag_edit', kwargs={'tag': done.extra['new']}))

        if action == 'undo':
            change = Action.objects.filter(id=request.POST.get('id') or 0, action_type__in=tagtools.TAG_ACTION_TYPES,
                                           canceled=False).first()
            change = change and change.leaf
            if not change or change.extra.get('tag_id') != tag.id or not change.can_undo():
                raise TagError(_("This change can no longer be undone."))
            change.cancel(user, ip)
            messages.info(request, _("The change was undone."))
            tag = Tag.objects.get(id=tag.id)  # an undone rename changes the name
            return HttpResponseRedirect(reverse('tag_edit', kwargs={'tag': tag.name}))
    except TagError as e:
        tag = Tag.objects.get(id=tag.id)
        return _manage_page(request, tag, error=str(e), **{action or 'other': request.POST.get(
            {'add_synonym': 'synonym', 'rename': 'name', 'add_version': 'version'}.get(action, ''), '')})

    return HttpResponseRedirect(edit_url)


VERSION_ACTIONS = ('add_version', 'remove_version', 'current_version', 'no_current_version', 'version_up',
                   'version_down')


def _version_change(request, tag, action, edit_url):
    """The tag's version list (forum/utils/tags.py); TagError goes back to the page."""
    from django.utils.html import escape
    from forum.search.filters import parse_date
    user, ip = request.user, _ip(request)
    if action == 'add_version':
        version = tagtools.add_version(tag, request.POST.get('version', ''), user, ip,
                                       current=bool(request.POST.get('current')),
                                       released=parse_date(request.POST.get('released', '')))
        messages.info(request, _("Version %(version)s added to %(tag)s.") % {
            'version': escape(version.name), 'tag': escape(tag.name)})
        return HttpResponseRedirect(edit_url + '#tag-versions-heading')
    version_id = request.POST.get('id', '')
    version = TagVersion.objects.filter(id=int(version_id), tag=tag).first() if version_id.isdigit() else None
    if version is None:
        raise TagError(_("This version no longer exists."))
    if action == 'remove_version':
        tagtools.remove_version(version, user, ip)
        messages.info(request, _("Version %s removed.") % escape(version.name))
    elif action in ('current_version', 'no_current_version'):
        tagtools.set_current_version(version, user, ip, current=action == 'current_version')
        if action == 'current_version':
            messages.info(request, _("%(version)s is now the current version of %(tag)s.") % {
                'version': escape(version.name), 'tag': escape(tag.name)})
        else:
            messages.info(request, _("No version of %s is marked as current.") % escape(tag.name))
    else:
        tagtools.move_version(version, -1 if action == 'version_up' else 1, user, ip)
    return HttpResponseRedirect(edit_url + '#tag-versions-heading')


def tag_merge(request, tag):
    """Merging `tag` into the tag named `into`: a summary to confirm (GET), then the merge (POST)."""
    _require_moderator(request)
    source = get_object_or_404(Tag, name=tag)
    into = (request.POST if request.method == 'POST' else request.GET).get('into', '')
    try:
        target = tagtools.merge_target(source, into)
    except TagError as e:
        return _manage_page(request, source, error=str(e), merge=into)

    if request.method == 'POST':
        done = tagtools.merge_tags(source, target, request.user, _ip(request))
        messages.info(request, _("%(source)s was merged into %(target)s.") % {
            'source': done.extra['source'], 'target': target.name})
        return HttpResponseRedirect(target.get_absolute_url())

    return render(request, 'tag_merge.html', {
        'source': source, 'target': target, 'summary': tagtools.merge_summary(source, target),
    })


def tag_synonyms(request):
    """Every synonym → tag pair, with a form to add one and remove buttons."""
    _require_moderator(request)
    user, ip = request.user, _ip(request)
    error, values = None, {}

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'remove':
            synonym = TagSynonym.objects.filter(id=request.POST.get('id')).select_related('target').first()
            if synonym:
                tagtools.remove_synonym(synonym, user, ip)
                messages.info(request, _("%s is no longer a synonym.") % synonym.source_name)
            return HttpResponseRedirect(reverse('tag_synonyms'))
        if action == 'add':
            values = {'synonym': request.POST.get('synonym', ''), 'tag': request.POST.get('tag', '')}
            name = tagtools.normalize(values['tag'])
            target = Tag.objects.filter(name=TagSynonym.resolve([name])[0]).first() if name else None
            if target is None:
                error = _("There is no tag named %s.") % name if name else _("Enter the tag the synonym stands for.")
            else:
                try:
                    done = tagtools.add_synonym(target, values['synonym'], user, ip)
                    messages.info(request, _("%(synonym)s is now a synonym of %(tag)s.") % {
                        'synonym': done.extra['synonym'], 'tag': target.name})
                    return HttpResponseRedirect(reverse('tag_synonyms'))
                except NeedsMerge as e:
                    return HttpResponseRedirect(merge_url(e.source, e.target))
                except TagError as e:
                    error = str(e)

    synonyms = TagSynonym.objects.select_related('target').order_by('target__name', 'source_name')
    query = request.GET.get('q', '').strip()
    if query:
        from django.db.models import Q
        synonyms = synonyms.filter(Q(source_name__icontains=query) | Q(target__name__icontains=query))
    return render(request, 'tag_synonyms.html', {
        'synonyms': synonyms, 'query': query, 'error': error, 'values': values,
    })
