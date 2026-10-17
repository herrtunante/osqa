from django import template

from forum.notifications import unread_count

register = template.Library()


@register.simple_tag(takes_context=True)
def unread_notifications(context):
    user = context.get('user')
    return unread_count(user) if user is not None else 0
