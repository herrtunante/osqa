"""Static files served by Django views (skin media, Django's admin media,
uploaded files) with HTTP caching.

- `{% media %}` URLs carry a version token, `?v=<hash of the file>`. A request
  whose token is the current one is answered with
  `Cache-Control: public, max-age=31536000, immutable`: the URL changes when
  the file does, so browsers and proxies never need to ask again.
- Any other request (no token, e.g. images referenced from CSS, Django's admin
  media; or an old token from a page saved before a deploy) gets a short
  max-age (`no-cache` for a token that doesn't match, so a proxy can't keep
  old content under a new URL during a rolling deploy) and is revalidated with
  ETag / Last-Modified, answered by 304 Not Modified.
- Uploaded files are never overwritten (the storage picks a new name), so
  they get the long cache too; files shipped with OSQA in forum/upfiles (the
  default logo) may change on upgrade and get the short one.
- CSS, JS, SVG and JSON (text that compresses well, never pages with a CSRF
  token: BREACH) are gzip-compressed when the browser accepts it; the
  compressed bytes are kept in memory per file version.

Tokens are content hashes (identical on every server of a deploy), computed
once per file and process; with DEBUG on, a changed file (mtime/size) gets a
new token without restarting.
"""
import gzip
import hashlib
import mimetypes
import os
import posixpath
import re
import threading

from django.conf import settings as django_settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import HttpResponse, HttpResponseNotModified
from django.utils._os import safe_join
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date, parse_http_date_safe
from django.views.static import serve

VERSION_PARAM = 'v'
LONG_CACHE = 'public, max-age=31536000, immutable'
SHORT_MAX_AGE = 3600
SHORT_CACHE = 'public, max-age=%d' % SHORT_MAX_AGE
STALE_TOKEN_CACHE = 'no-cache'

COMPRESSIBLE_TYPES = ('text/css', 'text/javascript', 'application/javascript', 'application/x-javascript',
                      'image/svg+xml', 'application/json', 'application/manifest+json')
MIN_COMPRESS_SIZE = 1024
MAX_COMPRESS_SIZE = 4 * 1024 * 1024
MAX_COMPRESSED_CACHE = 32 * 1024 * 1024

# Paths (without the script prefix) answered by the views of this module, and
# the service worker / manifest: files, the same for every visitor.
STATIC_PATHS = re.compile(r'^/(m/|upfiles/|sw\.js$|manifest\.webmanifest$|favicon\.ico$)')

_lock = threading.Lock()
_tokens = {}       # absolute path -> ((mtime_ns, size), token)
_compressed = {}   # (absolute path, token) -> gzip bytes
_compressed_size = [0]


def _stat_key(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    return stat.st_mtime_ns, stat.st_size


def file_token(path):
    """Short content hash of the file at `path`, or None when it doesn't exist."""
    known = _tokens.get(path)
    if known is not None and not django_settings.DEBUG:
        return known[1]
    key = _stat_key(path)
    if key is None:
        return None
    if known is not None and known[0] == key:
        return known[1]
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    token = digest.hexdigest()[:12]
    with _lock:
        _tokens[path] = (key, token)
    return token


def versioned(url, path):
    """`url` with the version token of the file at `path` added."""
    token = file_token(path)
    if not token:
        return url
    return '%s%s%s=%s' % (url, '&' if '?' in url else '?', VERSION_PARAM, token)


def _etag_matches(header, etags):
    if not header:
        return False
    if header.strip() == '*':
        return True
    candidates = [tag.strip() for tag in header.split(',')]
    candidates = [tag[2:] if tag.startswith('W/') else tag for tag in candidates]
    return any(tag in candidates for tag in etags)


def _gzipped(path, token):
    key = (path, token)
    data = _compressed.get(key)
    if data is None:
        with open(path, 'rb') as f:
            data = gzip.compress(f.read(), compresslevel=9, mtime=0)
        with _lock:
            if _compressed_size[0] + len(data) > MAX_COMPRESSED_CACHE:
                _compressed.clear()
                _compressed_size[0] = 0
            _compressed[key] = data
            _compressed_size[0] += len(data)
    return data


def serve_file(request, path, document_root, immutable=False):
    """Serve `path` from `document_root` with the caching rules above.

    `immutable`: the file never changes under this name (uploads), so every
    response may be cached for a year. Otherwise only a request carrying the
    current version token is.
    """
    try:
        fullpath = safe_join(document_root, posixpath.normpath(path).lstrip('/'))
    except (SuspiciousFileOperation, ValueError):
        fullpath = None
    token = fullpath and file_token(fullpath)
    if not token:
        # Missing file, directory or bad path: Django's view gives the 404/400.
        return serve(request, path, document_root=document_root)

    requested = request.GET.get(VERSION_PARAM)
    if immutable or requested == token:
        cache_control = LONG_CACHE
    elif requested:
        cache_control = STALE_TOKEN_CACHE
    else:
        cache_control = SHORT_CACHE

    content_type, encoding = mimetypes.guess_type(fullpath)
    content_type = content_type or 'application/octet-stream'
    if content_type.startswith('text/') or content_type in ('application/javascript', 'application/json'):
        content_type += '; charset=utf-8'
    size = os.path.getsize(fullpath)
    mtime = os.path.getmtime(fullpath)

    compress = (not encoding and content_type.split(';')[0] in COMPRESSIBLE_TYPES
                and MIN_COMPRESS_SIZE <= size <= MAX_COMPRESS_SIZE)
    use_gzip = compress and 'gzip' in request.META.get('HTTP_ACCEPT_ENCODING', '')
    etag = '"%s%s"' % (token, '-gz' if use_gzip else '')

    def finish(response):
        response['Cache-Control'] = cache_control
        response['ETag'] = etag
        response['Last-Modified'] = http_date(mtime)
        if compress:
            patch_vary_headers(response, ('Accept-Encoding',))
        return response

    if_none_match = request.META.get('HTTP_IF_NONE_MATCH')
    if if_none_match is not None:
        # Either representation's tag validates the file version.
        if _etag_matches(if_none_match, ('"%s"' % token, '"%s-gz"' % token)):
            return finish(HttpResponseNotModified())
    else:
        since = parse_http_date_safe(request.META.get('HTTP_IF_MODIFIED_SINCE') or '')
        if since is not None and int(mtime) <= since:
            return finish(HttpResponseNotModified())

    if use_gzip:
        response = HttpResponse(_gzipped(fullpath, token), content_type=content_type)
        response['Content-Encoding'] = 'gzip'
    else:
        response = serve(request, path, document_root=document_root)
        response['Content-Type'] = content_type
        if encoding:
            response['Content-Encoding'] = encoding
    response['Content-Length'] = str(len(response.content)) if use_gzip else str(size)
    return finish(response)


def is_static_request(request):
    return bool(STATIC_PATHS.match(request.path_info))
