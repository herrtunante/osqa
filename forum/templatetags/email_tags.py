"""Template tags for emails.

An email template wraps everything in {% email %}...{% endemail %}, which is
rendered once per recipient (context['recipient']) and sets {% subject %},
{% htmlcontent base %} and {% textcontent base %}. The subject and the plain
text part are rendered without HTML escaping (they are not HTML); the HTML
part escapes as usual, so user text (titles, names, post snippets) must reach
it unmarked. {% email_theme %} (in notifications/base.html) puts the inline
styles of the layout in the context (p_style, a_style, ...).
"""
import html as html_lib
import re

from django import template
from django.template.defaulttags import url as default_url
from django.utils.html import conditional_escape, strip_tags
from django.utils.safestring import mark_safe

from forum import settings
from forum.utils.mail import create_and_send_mail_messages

register = template.Library()


class MultiUserMailMessage(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        from forum.utils import unsubscribe

        recipients = context['recipients']
        kind = context.get('email_kind')  # set for notifications; account emails have none
        messages = list()

        for recipient in recipients:
            context['embeddedmedia'] = {}
            context['recipient'] = recipient
            links = unsubscribe.links(recipient, kind, context.get('email_question'),
                                      offer_summary=context.get('email_summary_offer', True)) if kind else None
            context['unsubscribe'] = links
            self.nodelist.render(context)
            headers = {}
            if links:
                # RFC 2369 / 8058: mail programs show their own one-click unsubscribe.
                headers = {'List-Unsubscribe': '<%s>' % links['all_url'],
                           'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'}
            messages.append((recipient, context['subject'], context['htmlcontent'], context['textcontent'],
                             context['embeddedmedia'], headers))

        # forum.utils.mail.render_template_email (the admin previews) collects the
        # messages instead of sending them.
        capture = context.get('email_capture')
        if capture is not None:
            capture.extend(messages)
            return ''

        errors = create_and_send_mail_messages(messages, sender_data=context['sender'], reply_to=context['reply_to'])
        if 'mail_errors' in context:
            context['mail_errors'].extend(errors or [])
        return ''

@register.tag
def email(parser, token):
    nodelist = parser.parse(('endemail',))
    parser.delete_first_token()
    return MultiUserMailMessage(nodelist)


def tidy_text(text):
    """Plain text part: no trailing or leading spaces on lines (template
    indentation), at most one empty line in a row."""
    lines = [line.strip() for line in text.replace('\r\n', '\n').split('\n')]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


class EmailPartNode(template.Node):
    def __init__(self, nodelist, varname):
        self.nodelist = nodelist
        self.varname = varname

    def render(self, context):
        plain = self.varname in ('subject', 'textcontent')
        autoescape = context.autoescape
        if plain:
            context.autoescape = False
        try:
            content = self.nodelist.render(context)
        finally:
            context.autoescape = autoescape
        if self.varname == 'subject':
            content = ' '.join(content.split())  # one line: no header injection, no template whitespace
        elif self.varname == 'textcontent':
            content = tidy_text(content)
        else:
            content = content.strip()
        context[self.varname] = content
        return ''

@register.tag
def subject(parser, token):
    nodelist = parser.parse(('endsubject',))
    parser.delete_first_token()
    return EmailPartNode(nodelist, 'subject')

def content(parser, token):
    try:
        tag_name, base = token.split_contents()
    except ValueError:
        try:
            tag_name = token.split_contents()[0]
            base = None
        except:
            raise template.TemplateSyntaxError("%r tag requires at least two arguments" % token.contents.split()[0])

    nodelist = parser.parse(('end%s' % tag_name,))

    if base:
        base = template.loader.get_template(base)

        basenodes = base.template.nodelist
        content = [i for i,n in enumerate(basenodes) if isinstance(n, template.loader_tags.BlockNode) and n.name == "content"]
        if len(content):
            index = content[0]
            nodelist = template.NodeList(basenodes[0:index] + nodelist + basenodes[index:])


    parser.delete_first_token()
    return EmailPartNode(nodelist, tag_name)


register.tag('htmlcontent', content)
register.tag('textcontent', content)


class PreheaderNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        # Rendered with the HTML part's escaping, so the result is safe HTML.
        context['preheader'] = mark_safe(' '.join(self.nodelist.render(context).split()))
        return ''


@register.tag
def preheader(parser, token):
    """{% preheader %}...{% endpreheader %}: the preview line shown after the
    subject in the inbox (hidden in the message). Put it before htmlcontent."""
    nodelist = parser.parse(('endpreheader',))
    parser.delete_first_token()
    return PreheaderNode(nodelist)


class EmbedMediaNode(template.Node):
    def __init__(self, location, alias):
        self.location = template.Variable(location)
        self.alias = alias

    def render(self, context):
        context['embeddedmedia'][self.alias] = self.location.resolve(context)
        return ''


@register.tag
def embedmedia(parser, token):
    try:
        tag_name, location, _, alias = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("%r tag requires exactly four arguments" % token.contents.split()[0])

    return EmbedMediaNode(location, alias)


class FullUrlNode(template.Node):
    def __init__(self, default_node):
        self.default_node = default_node

    def render(self, context):
        domain = settings.APP_BASE_URL
        path = self.default_node.render(context)
        asvar = getattr(self.default_node, 'asvar', None)
        if asvar:  # {% fullurl "name" ... as var %}
            context[asvar] = "%s%s" % (domain, context[asvar])
            return ''
        return "%s%s" % (domain, path)

@register.tag(name='fullurl')
def fullurl(parser, token):
    default_node = default_url(parser, token)
    return FullUrlNode(default_node)


# ---------------------------------------------------------------------------
# The layout: inline styles (email programs drop or ignore <style> blocks, so
# everything essential is inline; the <style> in notifications/base.html only
# adds dark mode and phone tweaks).

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
BRAND = '#2c5aa0'
TEXT = '#1f2328'
MUTED = '#59636e'

# The admin can add CSS to paragraphs and links (Email settings); these are the
# values the settings had before this layout, which mean "nothing added".
OLD_PARAGRAPH_STYLE = ("color:#333333;font-family:'helvetica neue', arial, Helvetica, sans-serif;line-height:18px;"
                       "font-size:14px;margin-top:10px;")
OLD_ANCHOR_STYLE = "text-decoration:none;color:#3060a8;font-weight:bold;"


def _custom(setting, old_default):
    value = str(setting or '').strip()
    if not value or value == old_default or value == str(setting.default).strip():
        return ''
    return value if value.endswith(';') else value + ';'


def email_styles():
    p = "margin:0 0 16px 0;font-family:%s;font-size:16px;line-height:24px;color:%s;" % (FONT, TEXT)
    a = "color:%s;text-decoration:underline;" % BRAND
    small = "margin:0 0 8px 0;font-family:%s;font-size:13px;line-height:20px;color:%s;" % (FONT, MUTED)
    return {
        'font_family': FONT,
        'brand_color': BRAND,
        'bg_color': str(settings.EMAIL_BORDER_COLOR or '').strip() or '#eef2f7',
        'p_style': p + _custom(settings.EMAIL_PARAGRAPH_STYLE, OLD_PARAGRAPH_STYLE),
        'a_style': a + _custom(settings.EMAIL_ANCHOR_STYLE, OLD_ANCHOR_STYLE),
        'h1_style': "margin:0 0 16px 0;font-family:%s;font-size:22px;line-height:30px;font-weight:700;color:%s;"
                    % (FONT, TEXT),
        'h2_style': "margin:24px 0 8px 0;font-family:%s;font-size:17px;line-height:24px;font-weight:700;color:%s;"
                    % (FONT, TEXT),
        'small_style': small,
        'small_a_style': "color:%s;text-decoration:underline;" % MUTED,
        'quote_style': "padding:12px 16px;background-color:#f3f6fa;border-left:4px solid %s;border-radius:4px;"
                       "font-family:%s;font-size:15px;line-height:23px;color:#30363d;" % (BRAND, FONT),
        'li_style': "margin:0 0 8px 0;font-family:%s;font-size:16px;line-height:24px;color:%s;" % (FONT, TEXT),
        'button_cell_style': "border-radius:8px;background-color:%s;" % BRAND,
        'button_style': "display:inline-block;padding:12px 24px;font-family:%s;font-size:16px;font-weight:600;"
                        "line-height:20px;color:#ffffff;text-decoration:none;border-radius:8px;" % FONT,
        'logo_url': absolute_url(settings.APP_LOGO) if str(settings.APP_LOGO or '').strip() else '',
    }


class EmailThemeNode(template.Node):
    def render(self, context):
        context.update(email_styles())
        return ''


@register.tag
def email_theme(parser, token):
    """Puts the layout's inline styles in the context (p_style, a_style, ...)."""
    return EmailThemeNode()


@register.filter
def absolute_url(value):
    """A site path as a full URL (APP_URL + path); full URLs are left alone."""
    value = str(value or '').strip()
    if re.match(r'^[a-z][a-z0-9+.-]*://', value, re.I) or value.startswith('mailto:'):
        return value
    if value.startswith(settings.APP_URL):
        return value
    return '%s%s%s' % (settings.APP_URL, '' if value.startswith('/') else '/', value)


_BLOCK_END = re.compile(r'<\s*(br|/p|/li|/h[1-6]|/pre|/div|/blockquote|/tr|/td|/th|/dd|/dt)\b[^>]*>', re.I)


def plain_text(value):
    """Post HTML as one line of plain text (entities decoded, never marked safe)."""
    text = _BLOCK_END.sub(' ', str(value or ''))
    text = html_lib.unescape(strip_tags(text))
    return ' '.join(text.split())


def truncate(text, length):
    if len(text) <= length:
        return text
    cut = text[:length]
    if ' ' in cut[length // 2:]:
        cut = cut.rsplit(' ', 1)[0]
    return cut.rstrip(' .,;:-') + '…'


@register.filter
def email_snippet(value, length=280):
    """The start of a post's text, for quoting in an email: plain text that the
    HTML part escapes and the text part shows as is."""
    return truncate(plain_text(value), int(length))


_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


@register.filter
def mention_context(value, username):
    """The sentence of a post that mentions @username (plain text), or the
    start of the post if the mention is not found in the text."""
    text = plain_text(value)
    needle = '@%s' % username
    lower = needle.lower()
    for sentence in _SENTENCE_END.split(text):
        position = sentence.lower().find(lower)
        if position < 0:
            continue
        if len(sentence) <= 300:
            return sentence
        # A very long sentence: the part around the mention.
        start = max(0, position - 120)
        part = sentence[start:]
        if start:
            part = '…' + part.split(' ', 1)[-1]
        return truncate(part, 300)
    return truncate(text, 280)


@register.filter
def highlight_mention(value, username):
    """Escapes plain text and puts @username in bold (HTML part only)."""
    escaped = str(conditional_escape(str(value)))
    needle = str(conditional_escape('@%s' % username))
    pattern = re.compile(re.escape(needle), re.I)
    return mark_safe(pattern.sub(lambda m: '<strong>%s</strong>' % m.group(0), escaped, count=1))
