"""{% confirm_banner %}: the "confirm your email address" banner under the
header, for members who can't post until they confirm (forum/utils/accounts.py)."""
from django import template

from forum.utils import accounts

register = template.Library()

# Pages that already say what the banner would.
QUIET_PAGES = {'send_validation_email', 'auth_validate_email', 'auth_check_email', 'logout', 'user_signout'}


@register.inclusion_tag('auth/confirm_banner.html', takes_context=True)
def confirm_banner(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    match = getattr(request, 'resolver_match', None)
    show = user is not None and accounts.shows_banner(user) and \
        getattr(match, 'url_name', None) not in QUIET_PAGES
    return {
        'show': show,
        'request': request,
        'csrf_token': context.get('csrf_token'),
        'email': getattr(user, 'email', '') if show else '',
    }
