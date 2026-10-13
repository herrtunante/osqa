"""Utilities for working with HTML."""
import re

import bleach
from urllib.parse import quote_plus
from django.utils.html import strip_tags, conditional_escape
from forum.utils.html2text import HTML2Text
from django.utils.safestring import mark_safe
from forum import settings

LANGUAGE_CLASS = re.compile(r'language-[\w+#.-]{1,30}')

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
    # class="mention" only styles @mention links; the data-user attribute that
    # notifications rely on is added after sanitizing and never accepted from users.
    'a': lambda tag, name, value: name == 'href' or (name == 'class' and value == 'mention'),
    'img': ['src'],
    # Fenced code blocks name their language for the syntax highlighter.
    'code': lambda tag, name, value: name == 'class' and LANGUAGE_CLASS.fullmatch(value) is not None,
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

def _attrs(attrs):
    return " ".join('%s="%s"' % (k, conditional_escape(v)) for k, v in attrs.items())

# content/title may contain user input (question titles, usernames); it is
# escaped unless it is already marked safe (e.g. a nested hyperlink).
def buildtag(name, content, **attrs):
    return mark_safe('<%s %s>%s</%s>' % (name, _attrs(attrs), conditional_escape(content), name))

def hyperlink(url, title, **attrs):
    return mark_safe('<a href="%s" %s>%s</a>' % (conditional_escape(url), _attrs(attrs), conditional_escape(title)))

def objlink(obj, **attrs):
    link = obj.get_absolute_url()
    if not link.startswith(settings.APP_URL):
        link = settings.APP_URL + link
    return hyperlink(link, str(obj), **attrs)
