"""Metadata for search engines and link previews."""
import json

from django import template
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe

from forum import settings

register = template.Library()

# Characters that would end the <script> element or confuse an HTML parser.
JSON_SCRIPT_ESCAPES = {ord('>'): '\\u003E', ord('<'): '\\u003C', ord('&'): '\\u0026'}

MAX_TEXT = 5000
MAX_SUGGESTED_ANSWERS = 5


def absolute(url):
    if '://' in url:
        return url
    return '%s%s' % (settings.APP_BASE_URL, url)


@register.simple_tag
def absolute_url(url):
    return absolute(str(url))


@register.simple_tag
def site_logo_url():
    logo = str(settings.APP_LOGO)
    if '://' in logo:
        return logo
    return absolute(logo if logo.startswith('/') else '/' + logo)


def _text(node):
    return ' '.join(strip_tags(str(node.html)).split())[:MAX_TEXT]


def _person(user):
    return {'@type': 'Person', 'name': user.username, 'url': absolute(user.get_profile_url())}


def _answer(answer):
    return {
        '@type': 'Answer',
        'text': _text(answer),
        'dateCreated': answer.added_at.isoformat(),
        'upvoteCount': answer.score,
        'url': absolute(answer.get_absolute_url()),
        'author': _person(answer.author),
    }


@register.simple_tag
def question_structured_data(question):
    """schema.org QAPage markup (https://developers.google.com/search/docs/appearance/structured-data/qapage)."""
    answers = list(question.answers.filter_state(deleted=False).order_by('-score', 'added_at')[:50])
    accepted = [a for a in answers if a.nis.accepted]
    others = [a for a in answers if not a.nis.accepted][:MAX_SUGGESTED_ANSWERS]

    data = {
        '@context': 'https://schema.org',
        '@type': 'QAPage',
        'mainEntity': {
            '@type': 'Question',
            'name': question.headline,
            'text': _text(question) or question.headline,
            'answerCount': question.answer_count,
            'upvoteCount': question.score,
            'dateCreated': question.added_at.isoformat(),
            'author': _person(question.author),
        },
    }
    if accepted:
        data['mainEntity']['acceptedAnswer'] = _answer(accepted[0])
    if others:
        data['mainEntity']['suggestedAnswer'] = [_answer(a) for a in others]

    payload = json.dumps(data, ensure_ascii=False).translate(JSON_SCRIPT_ESCAPES)
    return mark_safe('<script type="application/ld+json">%s</script>' % payload)
