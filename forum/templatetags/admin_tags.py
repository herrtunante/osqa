"""Tags for the administration layout (osqaadmin/base.html)."""
import html

from django import template
from django.template.loader_tags import BLOCK_CONTEXT_KEY
from django.utils.html import strip_tags

register = template.Library()


@register.simple_tag(takes_context=True)
def block_text(context, name):
    """The text of block `name` as the page defines it, rendered again: the
    page heading repeats the `subtitle` block of the <title> (a template
    can't use a block twice), so admin pages and module templates that only
    define `subtitle` get a heading too."""
    block_context = context.render_context.get(BLOCK_CONTEXT_KEY)
    block = block_context.get_block(name) if block_context else None
    if block is None:
        return ''
    # Plain text (escaped again by simple_tag), not HTML: it's a heading.
    return ' '.join(html.unescape(strip_tags(block.nodelist.render(context))).split())
