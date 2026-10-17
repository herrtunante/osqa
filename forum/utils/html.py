"""Utilities for working with HTML."""
import re

import html2text as _html2text
import nh3
from urllib.parse import quote_plus
from django.utils.html import strip_tags, conditional_escape
from django.utils.safestring import mark_safe

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
    '*': {'abbr', 'align', 'alt', 'axis', 'border',
          'cellpadding', 'cellspacing', 'char', 'charoff', 'charset', 'cite',
          'cols', 'colspan', 'datetime', 'dir', 'frame', 'headers', 'height',
          'hreflang', 'hspace', 'lang', 'longdesc', 'name', 'nohref',
          'noshade', 'nowrap', 'rel', 'rev', 'rows', 'rowspan', 'rules', 'scope',
          'span', 'start', 'summary', 'title', 'type', 'valign', 'vspace',
          'width'},
    'a': {'href', 'class'},
    'img': {'src'},
    'code': {'class'},
}


def _filter_attribute(tag, name, value):
    if name == 'class':
        # class="mention" only styles @mention links; the data-user attribute that
        # notifications rely on is added after sanitizing and never accepted from users.
        if tag == 'a':
            return value if value == 'mention' else None
        # Fenced code blocks name their language for the syntax highlighter.
        if tag == 'code':
            return value if LANGUAGE_CLASS.fullmatch(value) else None
    return value


def sanitize_html(html):
    """Sanitizes an HTML fragment: disallowed tags are stripped (keeping their
    text), disallowed attributes and unsafe URLs are dropped."""
    return nh3.clean(html, tags=set(ALLOWED_TAGS), attributes=ALLOWED_ATTRIBUTES,
                     attribute_filter=_filter_attribute, link_rel=None)

IMG_TAG = re.compile(r'<img(?=[\s/>])', re.IGNORECASE)


def lazy_images(html):
    """Adds loading="lazy" decoding="async" to the images of sanitized HTML.

    Only for HTML that went through sanitize_html, which never keeps a
    loading or decoding attribute from user input, so these are the only ones.
    """
    return IMG_TAG.sub('<img loading="lazy" decoding="async"', html)


# How far into a post's HTML its first image may start and still be fetched
# first (likely in the first screen, so the Largest Contentful Paint).
PRIORITY_IMAGE_WITHIN = 2000


def priority_image(html):
    """The question page's first image near the top of the post loads at once
    with high priority (it is often the LCP element); the others stay lazy.

    For stored post HTML (render_markdown()), whose images carry exactly the
    attributes lazy_images() adds."""
    match = IMG_TAG.search(html)
    if not match or match.start() > PRIORITY_IMAGE_WITHIN:
        return html
    start = match.start()
    rest = html[start:]
    if rest.startswith('<img loading="lazy" '):
        rest = '<img fetchpriority="high" ' + rest[len('<img loading="lazy" '):]
    else:
        rest = '<img fetchpriority="high"' + rest[4:]
    return html[:start] + rest


def sanitize_profile_html(html, links=True):
    """Like sanitize_html, for a user's "about me": never images; links only
    when `links`, and then marked nofollow/ugc so they pass no search ranking."""
    attributes = {tag: set(names) - {'rel'} for tag, names in ALLOWED_ATTRIBUTES.items()}
    tags = set(ALLOWED_TAGS) - {'img'}
    if not links:
        tags.discard('a')
    return nh3.clean(html, tags=tags, attributes=attributes, attribute_filter=_filter_attribute,
                     link_rel='nofollow ugc noopener noreferrer' if links else None)

def cleanup_urls(url):
    return quote_plus(strip_tags(url))


def html2text(s, page_width=80):
    """Converts an HTML fragment into readable plain text (Markdown-style)."""
    return mark_safe(_html2text.HTML2Text(bodywidth=page_width).handle(s))

def _attrs(attrs):
    return " ".join('%s="%s"' % (k, conditional_escape(v)) for k, v in attrs.items())

# content/title may contain user input (question titles, usernames); it is
# escaped unless it is already marked safe (e.g. a nested hyperlink).
def buildtag(name, content, **attrs):
    return mark_safe('<%s %s>%s</%s>' % (name, _attrs(attrs), conditional_escape(content), name))

def hyperlink(url, title, **attrs):
    return mark_safe('<a href="%s" %s>%s</a>' % (conditional_escape(url), _attrs(attrs), conditional_escape(title)))

