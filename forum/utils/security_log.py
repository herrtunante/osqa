"""The security log: sign-ins that fail, password changes, refused pages and
changes to permissions and site settings.

Everything goes to the 'forum.security' logger (see LOGGING in
settings_local.py.dist), one line per event as key=value pairs. Passwords and
tokens are never logged.
"""
import logging

log = logging.getLogger('forum.security')


def _who(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return 'anonymous'
    return '%s(id=%s)' % (user.username, user.id)


def event(name, request=None, level=logging.INFO, **fields):
    parts = ['event=%s' % name]
    if request is not None:
        parts.append('ip=%s' % request.META.get('REMOTE_ADDR', ''))
        parts.append('user=%s' % _who(getattr(request, 'user', None)))
    for key, value in fields.items():
        if hasattr(value, 'username'):
            parts.append('%s=%s' % (key, _who(value)))
        else:
            # Text is quoted so values typed by visitors can't fake other fields.
            parts.append('%s=%r' % (key, value) if isinstance(value, str) else '%s=%s' % (key, value))
    log.log(level, ' '.join(parts))
