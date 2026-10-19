"""{% live_updates question %}: the live question page (forum/utils/live.py,
media/js/osqa.live.js)."""
import json

from django import template
from django.urls import reverse
from django.utils.translation import gettext as _, ngettext

from forum import settings
from forum.utils import live

register = template.Library()


def _forms(translate):
    # The script picks the form: [one, other] with %(count)s in each.
    # Each call names its strings literally so makemessages finds them.
    return [translate(1), translate(2)]


@register.inclusion_tag('node/live_updates.html', takes_context=True)
def live_updates(context, question):
    request = context.get('request')
    enabled = request is not None and not live.is_crawler(request)
    texts = {
        'answers': _forms(lambda n: ngettext('%(count)s new answer', '%(count)s new answers', n)),
        'edits': _forms(lambda n: ngettext('%(count)s edited post', '%(count)s edited posts', n)),
        'comments': _forms(lambda n: ngettext('%(count)s new comment', '%(count)s new comments', n)),
        'show': _('Show'),
        'show_label': _('Show the new and edited posts'),
        'writing': _('A new answer was posted while you were writing.'),
        'writing_link': _('Read it'),
        'shown': _('The new posts are shown.'),
    }
    return {
        'enabled': enabled,
        'url': reverse('question_updates', kwargs={'id': question.id}),
        'since': live.now_token(),
        'interval': live.POLL_SECONDS,
        'latest_first': bool(settings.SHOW_LATEST_COMMENTS_FIRST),
        'texts': json.dumps(texts),
    }
