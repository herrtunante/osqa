from django import template

from forum.utils.support import format_hours

register = template.Library()


@register.filter
def hours(value):
    """3.5 -> '3.5 h', 60 -> '2.5 d'."""
    return format_hours(value)
