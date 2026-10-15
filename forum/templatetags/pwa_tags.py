from django import template
from django.urls import reverse
from django.utils.html import format_html

from forum import settings
from forum.templatetags.extra_tags import media
from forum.views import pwa

register = template.Library()


@register.simple_tag(takes_context=True)
def pwa_head(context):
    """Manifest, theme colours, touch icon and the script that registers the
    service worker (it is told who is signed in, to empty the saved pages
    when that changes)."""
    user = context.get('user')
    user_id = user.pk if user is not None and user.is_authenticated else ''
    if settings.DARK_MODE:
        theme = format_html(
            '<meta name="theme-color" content="{}" media="(prefers-color-scheme: light)" />\n        '
            '<meta name="theme-color" content="{}" media="(prefers-color-scheme: dark)" />',
            pwa.THEME_COLOR, pwa.THEME_COLOR_DARK)
    else:
        theme = format_html('<meta name="theme-color" content="{}" />', pwa.THEME_COLOR)
    return format_html(
        '{}\n        <link rel="manifest" href="{}" />\n        <link rel="apple-touch-icon" href="{}" />\n        '
        '<script defer src="{}" data-user="{}"></script>',
        theme, reverse('manifest'), pwa.touch_icon_url(), media('/media/js/osqa.pwa.js'), user_id)
