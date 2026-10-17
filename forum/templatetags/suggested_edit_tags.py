from django import template

from forum.utils import suggested_edits

register = template.Library()


@register.simple_tag
def suggested_edits_waiting():
    """Pending suggested edits on published posts (cached for a minute)."""
    return suggested_edits.pending_count()
