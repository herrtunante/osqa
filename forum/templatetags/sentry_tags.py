from django import template
from django.utils.html import format_html, json_script

from forum.templatetags.extra_tags import media
from forum.utils.sentry import browser_config

register = template.Library()


@register.simple_tag(takes_context=True)
def sentry_browser(context):
    """The Sentry browser SDK and its settings, when SENTRY_FRONTEND_DSN is set.
    Loaded first in <head> so errors in the other scripts are caught too."""
    request = context.get('request')
    config = browser_config(request) if request is not None else None
    if config is None:
        return ''
    return format_html('{}\n        <script src="{}"></script>\n        <script src="{}"></script>',
                       json_script(config, 'sentry-config'),
                       media('/media/vendor/sentry-10.75.3/sentry.bundle.min.js'),
                       media('/media/js/osqa.sentry.js'))
