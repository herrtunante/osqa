"""A small, polite documentation crawler for the knowledge index.

From each seed URL it follows links that stay on the same host and under the
seed's path, honours robots.txt, fetches only HTML and turns each page into
plain text (navigation, scripts and footers removed).
"""
import logging
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlsplit
from urllib.robotparser import RobotFileParser

import html2text
import nh3

from forum import settings

log = logging.getLogger(__name__)

SKIP_EXTENSIONS = ('.pdf', '.zip', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.mp4', '.mp3', '.xls', '.xlsx',
                   '.doc', '.docx', '.ppt', '.pptx', '.exe', '.jar', '.gz', '.tar', '.kmz', '.kml', '.csv', '.json', '.xml')
TEXT_TAGS = {'a', 'p', 'br', 'div', 'span', 'section', 'article', 'main', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
             'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'pre', 'code', 'blockquote', 'table', 'thead', 'tbody', 'tr',
             'th', 'td', 'em', 'strong', 'b', 'i'}
DROP_WITH_CONTENT = {'script', 'style', 'nav', 'header', 'footer', 'noscript', 'svg', 'form', 'aside', 'button', 'template', 'title'}
MAX_PAGE_BYTES = 2 * 1024 * 1024


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.title, self._in_title = [], '', False

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)
        elif tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def page_text(html):
    """Readable text of an HTML page."""
    cleaned = nh3.clean(html, tags=TEXT_TAGS, clean_content_tags=DROP_WITH_CONTENT, attributes={}, link_rel=None)
    converter = html2text.HTML2Text(bodywidth=0)
    converter.ignore_links = True
    converter.ignore_images = True
    return '\n'.join(line.rstrip() for line in converter.handle(cleaned).splitlines()).strip()


def user_agent():
    return 'OSQA-knowledge-crawler/1.0 (+%s)' % settings.APP_URL


def fetch(url, timeout=20):
    """(final_url, html) for an HTML page, or None."""
    request = urllib.request.Request(url, headers={'User-Agent': user_agent(), 'Accept': 'text/html'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if 'html' not in response.headers.get('Content-Type', ''):
                return None
            data = response.read(MAX_PAGE_BYTES + 1)
            if len(data) > MAX_PAGE_BYTES:
                return None
            charset = response.headers.get_content_charset() or 'utf-8'
            return response.geturl(), data.decode(charset, errors='replace')
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.info("Crawler could not fetch %s: %s", url, e)
        return None


def _robots(base, cache):
    if base not in cache:
        parser = RobotFileParser()
        fetched = None
        request = urllib.request.Request(base + '/robots.txt', headers={'User-Agent': user_agent()})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                fetched = response.read(512 * 1024).decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            fetched = '' if e.code in (404, 410) else None  # no robots.txt: everything allowed
        except (urllib.error.URLError, OSError, ValueError):
            fetched = None
        if fetched is None:
            parser.disallow_all = True  # robots.txt unreachable: stay away
        else:
            parser.parse(fetched.splitlines())
        cache[base] = parser
    return cache[base]


def in_scope(url, seed):
    u, s = urlsplit(url), urlsplit(seed)
    if u.scheme not in ('http', 'https') or u.netloc.lower() != s.netloc.lower():
        return False
    prefix = s.path if s.path.endswith('/') else s.path.rsplit('/', 1)[0] + '/'
    return u.path.startswith(prefix) and not u.path.lower().endswith(SKIP_EXTENSIONS)


def crawl(seed, max_pages, delay=0.5, fetcher=None, robots_cache=None):
    """Yields (url, title, text) for up to `max_pages` pages reachable from `seed`."""
    robots_cache = {} if robots_cache is None else robots_cache
    fetcher = fetcher or fetch
    own_host = urlsplit(str(settings.APP_URL)).netloc.lower()
    queue, seen, fetched = [urldefrag(seed)[0]], set(), 0
    while queue and fetched < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        parts = urlsplit(url)
        if own_host and parts.netloc.lower() == own_host:
            continue  # the forum itself is indexed from the database
        if not _robots('%s://%s' % (parts.scheme, parts.netloc), robots_cache).can_fetch(user_agent(), url):
            continue
        result = fetcher(url)
        fetched += 1
        if delay:
            time.sleep(delay)
        if result is None:
            continue
        final_url, html = result
        parser = _LinkParser()
        try:
            parser.feed(html)
        except Exception:
            pass
        for href in parser.links:
            link = urldefrag(urljoin(final_url, href))[0]
            if link not in seen and in_scope(link, seed):
                queue.append(link)
        text = page_text(html)
        if len(text) >= 200:
            yield final_url, ' '.join(parser.title.split()) or final_url, text
