"""The visitor's display preferences on the <html> element and in <head>
(forum/utils/display.py): used by base_content.html and osqaadmin/base.html.

    <html lang="…"{% display_attrs %}>
    <head>… {% pwa_head %} {% theme_head %} …

data-theme is the resolved theme ("light" or "dark"), the one attribute the
dark rules of osqa.css and admin.css (and Django admin's own CSS) depend on;
data-theme-choice is what the visitor picked ("auto", "light", "dark").
data-color-scheme="auto" (choice "auto") is kept for older Custom CSS written
as `@media (prefers-color-scheme: dark) { :root[data-color-scheme="auto"] … }`.
"""
from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import pgettext

from forum import settings
from forum.utils import display

register = template.Library()

COLOR_SCHEMES = {'auto': 'light dark', 'light': 'light', 'dark': 'dark'}

# Resolves "auto" before the first paint, follows the device setting while
# the choice is "auto", and gives the theme switch (osqa.theme.js) apply().
# Anonymous visitors' cookie wins over the page (a copy the service worker or
# the back/forward cache kept may be older than their last choice).
HEAD_SCRIPT = '''(function (d, root, fromCookie) {
    var query = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    var media = {auto: ['(prefers-color-scheme: light)', '(prefers-color-scheme: dark)'],
                 light: ['all', 'not all'], dark: ['not all', 'all']};
    var schemes = {auto: 'light dark', light: 'light', dark: 'dark'};
    function cookie() {
        var found = /(?:^|;\\s*)osqa_theme=(auto|light|dark)(?:;|$)/.exec(d.cookie);
        return found && found[1];
    }
    function apply(choice) {
        if (!media[choice]) choice = 'auto';
        var theme = choice === 'auto' ? (query && query.matches ? 'dark' : 'light') : choice;
        var changed = root.getAttribute('data-theme') !== theme || root.getAttribute('data-theme-choice') !== choice;
        root.setAttribute('data-theme', theme);
        root.setAttribute('data-theme-choice', choice);
        if (choice === 'auto') root.setAttribute('data-color-scheme', 'auto');
        else root.removeAttribute('data-color-scheme');
        var metas = d.querySelectorAll('meta[name="theme-color"]');
        if (metas.length === 2) {
            metas[0].setAttribute('media', media[choice][0]);
            metas[1].setAttribute('media', media[choice][1]);
        }
        var scheme = d.querySelector('meta[name="color-scheme"]');
        if (scheme) scheme.setAttribute('content', schemes[choice]);
        if (changed && window.CustomEvent) {
            d.dispatchEvent(new CustomEvent('osqa:themechange', {detail: {theme: theme, choice: choice}}));
        }
        return theme;
    }
    apply((fromCookie && cookie()) || root.getAttribute('data-theme-choice'));
    if (query) {
        var follow = function () { apply(root.getAttribute('data-theme-choice')); };
        if (query.addEventListener) query.addEventListener('change', follow);
        else if (query.addListener) query.addListener(follow);
    }
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) apply(cookie() || root.getAttribute('data-theme-choice'));
    });
    window.osqaTheme = {
        apply: apply,
        choice: function () { return root.getAttribute('data-theme-choice'); },
        isDark: function () { return root.getAttribute('data-theme') === 'dark'; }
    };
})(document, document.documentElement, %s);'''


@register.simple_tag(takes_context=True)
def display_attrs(context):
    request = context.get('request')
    attrs = []
    if settings.DARK_MODE:
        choice = display.theme(request)
        resolved = 'dark' if choice == 'dark' else 'light'  # "auto": the head script decides
        attrs += [('data-theme', resolved), ('data-theme-choice', choice)]
        if choice == 'auto':
            attrs.append(('data-color-scheme', 'auto'))
    if not display.shortcuts_enabled(request):
        attrs.append(('data-shortcuts', 'off'))
    return mark_safe(''.join(format_html(' {}="{}"', name, value) for name, value in attrs))


@register.simple_tag(takes_context=True)
def theme_head(context):
    """The color-scheme meta tag and the theme script (nothing while the
    site's dark mode is off). Place it after {% pwa_head %} (theme-color)."""
    if not settings.DARK_MODE:
        return ''
    request = context.get('request')
    choice = display.theme(request)
    user = getattr(request, 'user', None)
    from_cookie = 'false' if user is not None and user.is_authenticated else 'true'
    return format_html('<meta name="color-scheme" content="{}" />\n        <script nonce="{}">{}</script>',
                       COLOR_SCHEMES[choice], context.get('csp_nonce', ''),
                       mark_safe(HEAD_SCRIPT % from_cookie))


@register.simple_tag(takes_context=True)
def theme_options(context):
    """[(value, label, selected)] for the theme switch templates (display/)."""
    choice = display.theme(context.get('request'))
    return [(value, label, value == choice) for value, label in (
        ('auto', pgettext('theme', 'System')), ('light', pgettext('theme', 'Light')), ('dark', pgettext('theme', 'Dark')))]


@register.simple_tag(takes_context=True)
def shortcuts_enabled(context):
    return display.shortcuts_enabled(context.get('request'))
