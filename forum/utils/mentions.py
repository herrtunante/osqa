"""@username mentions in posts and comments.

Rendering turns "@name" into a link to the user's profile (with the user id
in data-user) when "name" is an existing username; notifications read the
ids back from the stored HTML. Usernames may contain spaces, so the longest
existing username at each "@" wins ("@John Smith said" -> "John Smith").
"""
import re

from django.db.models.functions import Lower
from django.utils.html import escape

MAX_USERNAME = 30

# "@" not preceded by a word character (so e-mail addresses do not count).
MENTION = re.compile(r'(?<![\w@/.])@([\w][\w\- ]{0,%d})' % (MAX_USERNAME - 1), re.UNICODE)
# Split HTML into tags and text; mentions are only looked for in text.
TAG = re.compile(r'(<[^>]*>)')
SKIP_TAGS = ('a', 'code', 'pre')
LINKED = re.compile(r'<a class="mention" data-user="(\d+)"')


def _candidates(text):
    """Every prefix of the text after an "@" that ends at a word boundary, longest first."""
    words = re.split(r'(\s+)', text.rstrip())
    out = []
    for end in range(len(words), 0, -1):
        candidate = ''.join(words[:end]).rstrip()
        # A trailing hyphen is punctuation ("@bob- thanks"), not part of the name.
        candidate = candidate.rstrip('-')
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _text_segments(html):
    """Yield (index, text) for the parts of html outside <a>, <code> and <pre>."""
    parts = TAG.split(html)
    depth = 0
    for i, part in enumerate(parts):
        if part.startswith('<'):
            match = re.match(r'<\s*(/)?\s*([a-zA-Z0-9]+)', part)
            if match and match.group(2).lower() in SKIP_TAGS and not part.endswith('/>'):
                depth += -1 if match.group(1) else 1
                depth = max(depth, 0)
        elif depth == 0 and '@' in part:
            yield parts, i, part


def link_mentions(html):
    """Return (html with mentions linked, list of mentioned users)."""
    from forum.models import User

    segments = list(_text_segments(html))
    if not segments:
        return html, []

    wanted = set()
    for parts, i, text in segments:
        for match in MENTION.finditer(text):
            wanted.update(c.lower() for c in _candidates(match.group(1)))
    if not wanted:
        return html, []

    users = {u.lname: u for u in User.objects.annotate(lname=Lower('username'))
                                             .filter(lname__in=wanted, is_active=True)}
    if not users:
        return html, []

    mentioned = {}
    parts = segments[0][0]
    for _, i, text in segments:
        def replace(match):
            for candidate in _candidates(match.group(1)):
                user = users.get(candidate.lower())
                if user:
                    mentioned[user.id] = user
                    rest = match.group(1)[len(candidate):]
                    return '<a class="mention" data-user="%d" href="%s">@%s</a>%s' % (
                        user.id, escape(user.get_profile_url()), escape(user.username), rest)
            return match.group(0)
        parts[i] = MENTION.sub(replace, text)

    return ''.join(parts), list(mentioned.values())


def mentioned_user_ids(html):
    """Ids of the users linked as mentions in rendered (stored) HTML."""
    return {int(i) for i in LINKED.findall(html or '')}
