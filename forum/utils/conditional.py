"""Conditional GET (ETag / 304 Not Modified) for pages anonymous visitors see.

Pages differ by user, language, pending messages and CSRF token, so an ETag
is only given to an anonymous visitor with no pending message, and it is made
of everything else the page depends on:

- the URL with its query string and the interface language;
- the time stamps the object and query caches keep for posts, users, tags and
  settings (they change whenever any of them is saved, so the sidebar, counts
  and names are never older than the last change on the site);
- the session's CSRF secret (CSRF_USE_SESSIONS), so a copy whose token no
  longer matches the session is never reused;
- the skin media version (a deploy changes the page's asset URLs);
- the current hour, so relative dates ("2 hours ago") and view counts, which
  change without any of the above, are at most an hour old.

Responses get `Cache-Control: private, no-cache` (browsers revalidate every
time, shared caches never keep them) and `Vary: Cookie, Accept-Language`.
When the cache can't keep the time stamps (a dummy cache), no ETag is given.
"""
import datetime
import hashlib

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponseNotModified
from django.middleware.csrf import CSRF_SESSION_KEY
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.translation import get_language


def _stamps():
    """The cache invalidation time stamps of posts, users, tags and settings.

    A missing one is set to now, as the query cache does when it finds none
    (it then treats every cached query of that model as out of date)."""
    from forum.models import Node, User, Tag, KeyValue
    models = (Node, User, Tag, KeyValue)
    keys = [model._get_cache_query_invalidation_key() for model in models]
    stamps = cache.get_many(keys)
    for model, key in zip(models, keys):
        if key not in stamps:
            model._set_query_cache_invalidation_timestamp()
            stamps[key] = cache.get(key)
    return [stamps[key] for key in keys]


def anonymous_page_etag(request, *parts, shows_messages=True):
    """The ETag of the page for this request, or None when it must not have one.
    `shows_messages`: the page shows pending messages (HTML pages; not feeds)."""
    if request.method not in ('GET', 'HEAD') or request.user.is_authenticated:
        return None
    if shows_messages and len(messages.get_messages(request)):
        return None
    stamps = _stamps()
    if None in stamps:
        return None
    from forum.views.pwa import version as media_version
    raw = '|'.join(str(part) for part in [
        request.get_full_path(), get_language(), request.session.get(CSRF_SESSION_KEY, ''),
        media_version(), datetime.datetime.now().strftime('%Y%m%d%H'),
    ] + [stamp.isoformat() for stamp in stamps] + list(parts))
    return '"%s"' % hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def not_modified(request, etag):
    """A 304 response when the browser's copy has `etag`, else None."""
    if etag is None:
        return None
    header = request.META.get('HTTP_IF_NONE_MATCH', '')
    if etag in [tag.strip() for tag in header.split(',')]:
        return finish(HttpResponseNotModified(), etag)
    return None


def finish(response, etag):
    if etag is not None and response.status_code in (200, 304):
        response['ETag'] = etag
        patch_cache_control(response, private=True, no_cache=True)
        patch_vary_headers(response, ('Cookie', 'Accept-Language'))
    return response
