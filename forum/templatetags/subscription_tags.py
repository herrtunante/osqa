from django import template

from forum.models import MarkedTag

register = template.Library()


@register.simple_tag
def tag_follow_state(tag, user):
    """Whether `user` follows `tag`, how many do, and whether they'd get emails."""
    state = {'followers': MarkedTag.objects.filter(tag=tag, reason='good').count(), 'following': False}
    if user.is_authenticated:
        state['following'] = MarkedTag.objects.filter(tag=tag, user=user, reason='good').exists()
        prefs = user.subscription_settings
        state['emails_off'] = not prefs.enable_notifications
    return state
