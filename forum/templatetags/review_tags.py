from django import template

from forum.utils import review

register = template.Library()


@register.simple_tag
def moderation_counts():
    """What waits in the moderation inbox (cached for a minute): review, flags, ai, users, total."""
    return review.inbox_counts()
