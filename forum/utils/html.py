"""Utilities for working with HTML."""
import bleach
from urllib.parse import quote_plus
from django.utils.html import strip_tags
from forum.utils.html2text import HTML2Text
from django.utils.safestring import mark_safe
from forum import settings

# Define allowed HTML tags and attributes for sanitization
ALLOWED_TAGS = [
    'a', 'abbr', 'acronym', 'address', 'b', 'big',
    'blockquote', 'br', 'caption', 'center', 'cite', 'code', 'col',
    'colgroup', 'dd', 'del', 'dfn', 'dir', 'div', 'dl', 'dt', 'em', 'font',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'kbd',
    'li', 'ol', 'p', 'pre', 'q', 's', 'samp', 'small', 'span', 'strike',
    'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead',
    'tr', 'tt', 'u', 'ul', 'var'
]

ALLOWED_ATTRIBUTES = {
    '*': ['abbr', 'align', 'alt', 'axis', 'border',
          'cellpadding', 'cellspacing', 'char', 'charoff', 'charset', 'cite',
          'cols', 'colspan', 'datetime', 'dir', 'frame', 'headers', 'height',
          'hreflang', 'hspace', 'lang', 'longdesc', 'name', 'nohref',
          'noshade', 'nowrap', 'rel', 'rev', 'rows', 'rowspan', 'rules', 'scope',
          'span', 'start', 'summary', 'title', 'type', 'valign', 'vspace',
          'width'],
    'a': ['href'],
    'img': ['src']
}

def sanitize_html(html):
    """Sanitizes an HTML fragment using bleach."""
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)

def cleanup_urls(url):
    return quote_plus(strip_tags(url))


def html2text(s, ignore_tags=(), indent_width=4, page_width=80):
    ignore_tags = [t.lower() for t in ignore_tags]
    parser = HTML2Text(ignore_tags, indent_width, page_width)
    parser.feed(s)
    parser.close()
    parser.generate()
    return mark_safe(parser.result)

def buildtag(name, content, **attrs):
    return mark_safe('<%s %s>%s</%s>' % (name, " ".join('%s="%s"' % i for i in attrs.items()), str(content), name))

def hyperlink(url, title, **attrs):
    return mark_safe('<a href="%s" %s>%s</a>' % (url, " ".join('%s="%s"' % i for i in attrs.items()), title))

def objlink(obj, **attrs):
    link = obj.get_absolute_url()
    if not link.startswith(settings.APP_URL):
        link = settings.APP_URL + link
    return hyperlink(link, str(obj), **attrs)
