"""The versions posts apply to (forum/utils/post_versions.py) in templates.
None of these runs a query per post: the question page's versions are loaded
once per request (post_versions.for_thread) and list rows read what the
list's prefetch gave them."""
from django import template
from django.template.loader import render_to_string
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

from forum.utils import post_versions as versions

register = template.Library()


def _chips(post_versions):
    if not post_versions:
        return ''
    return format_html('<span class="version-chips"><span class="visually-hidden">{}</span>{}</span>',
                       _('Applies to:'),
                       format_html_join('', ' <span class="version-chip">{}</span>',
                                        ((pv.version.label,) for pv in post_versions)))


@register.simple_tag(takes_context=True)
def post_versions(context, post, user):
    """An answer's version chips, the "may be outdated" notice with "Still
    works in <current>" for members who may edit it, and the confirmation
    (node/post_versions.html, included by node/answer.html)."""
    found = versions.of_post(post)
    if not found:
        return ''
    items = [{'pv': pv, 'can_confirm': versions.can_confirm(user, post, pv), 'can_undo': versions.can_undo(user, pv)}
             for pv in found]
    return render_to_string('node/post_versions.html', {
        'post': post, 'items': items, 'chips': _chips(found), 'csrf_token': context.get('csrf_token'),
    })


@register.simple_tag
def question_version_chips(question):
    """The question's version chips (next to its tags on the question page)."""
    return _chips(versions.of_post(question))


@register.simple_tag
def list_version_chips(question):
    """A list row's version chips, when the list prefetched them."""
    return _chips(getattr(question, 'osqa_versions', None))
