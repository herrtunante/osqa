"""Plain text of posts for the index, and result snippets with the matched
words in <mark>. Snippets are built from the plain text and escaped piece by
piece: post HTML never reaches the results page."""
import re
from html.parser import HTMLParser

from django.utils.html import escape
from django.utils.safestring import mark_safe

# Each indexed field is capped: a huge post shouldn't make a huge row.
MAX_FIELD = 100000
SNIPPET_LENGTH = 240

BLOCK_TAGS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'pre', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'table', 'tr', 'td', 'th', 'hr', 'dd', 'dt'}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append(' ')

    handle_endtag = lambda self, tag: self.handle_starttag(tag, None)

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html, limit=MAX_FIELD):
    """The text of an HTML fragment, entities decoded, whitespace collapsed."""
    if not html:
        return ''
    parser = _TextExtractor()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception:
        return ''
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()[:limit]


def stem(word):
    """The word without a plural/verb ending, so 'masking' marks 'mask', 'masks'..."""
    for suffix in ('ing', 'ed', 'es', 's'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def highlighter(terms):
    """A regex matching any of `terms` (lists of words: a word or a phrase) at
    word starts, with endings allowed; None without terms."""
    patterns = []
    for term in terms:
        if not term:
            continue
        pattern = r'\W+'.join(re.escape(stem(w)) for w in term) + r'\w*'
        if pattern not in patterns:
            patterns.append(pattern)
    if not patterns:
        return None
    patterns.sort(key=len, reverse=True)
    return re.compile(r'(?<!\w)(?:%s)' % '|'.join(patterns), re.IGNORECASE | re.UNICODE)


def mark(text, regex):
    """`text` escaped, with each match of `regex` in <mark>."""
    if regex is None:
        return mark_safe(escape(text))
    out, last = [], 0
    for match in regex.finditer(text):
        out.append(escape(text[last:match.start()]))
        out.append('<mark>%s</mark>' % escape(match.group(0)))
        last = match.end()
    out.append(escape(text[last:]))
    return mark_safe(''.join(out))


def excerpt(text, regex, length=SNIPPET_LENGTH):
    """(fragment, matched): about `length` characters of `text` around the
    first match, cut at word boundaries, with ellipses where it was cut."""
    match = regex.search(text) if regex is not None else None
    if len(text) <= length:
        return text, bool(match)
    start = 0
    if match:
        start = max(0, match.start() - length // 3)
        if start:
            space = text.find(' ', start)
            start = space + 1 if 0 <= space < match.start() else start
    end = min(len(text), start + length)
    if end < len(text):
        space = text.rfind(' ', start, end)
        end = space if space > (match.end() if match else start) else end
    return ('… ' if start else '') + text[start:end] + (' …' if end < len(text) else ''), bool(match)


def snippet(regex, *texts):
    """A marked snippet from the first of `texts` that contains a match
    (else from the first non-empty one)."""
    texts = [t for t in texts if t]
    if not texts:
        return ''
    for text in texts:
        fragment, matched = excerpt(text, regex)
        if matched:
            return mark(fragment, regex)
    return mark(excerpt(texts[0], None)[0], None)
