from django import template
from django.utils.safestring import mark_safe
import logging
import markdown
import re

register = template.Library()

@template.defaultfilters.stringfilter
@register.filter
def collapse(input):
    return ' '.join(input.split())


@register.filter
def priority_image(html):
    """Post HTML whose first image (near the top) loads at once, with high
    priority: the question body, often the page's largest paint."""
    from forum.utils.html import priority_image as prioritize
    return mark_safe(prioritize(str(html)))


@register.filter
def can_edit_post(user, post):
    return user.can_edit_post(post)


@register.filter
def decorated_int(number, cls="thousand"):
    try:
        number = int(number)    # allow strings or numbers passed in
        if number > 999:
            thousands = float(number) / 1000.0

            if number < 99500:
                format = "%.1f"
            else:
                format = "%.0f"

            s = format % thousands

            return mark_safe("<span class=\"%s\">%sk</span>" % (cls, s))
        return number
    except:
        return number

@register.filter
def or_preview(setting, request):
    if request.user.is_superuser:
        previewing = request.session.get('previewing_settings', {})
        if setting.name in previewing:
            return previewing[setting.name]

    return setting.value

@register.filter
def getval(map, key):
    return map and map.get(key, None) or None


@register.filter
def contained_in(item, container):
    return item in container

@register.filter
def static_content(content, render_mode):
    if render_mode == 'markdown':
        return mark_safe(markdown.markdown(str(content), extensions=['forum.markdownext.mdx_settingsparser']))
    elif render_mode == 'markdown-safe':
        # Markdown 3.x+ has no safe_mode: render, then sanitize. (Escaping the
        # rendered HTML showed visitors the raw tags.)
        from forum.utils.html import sanitize_html
        return mark_safe(sanitize_html(markdown.markdown(str(content))))
    elif render_mode == "html":
        return mark_safe(str(content))
    else:
        return str(content)


_SCRIPT_WITHOUT_NONCE = re.compile(r'<script\b(?![^>]*\snonce=)', re.IGNORECASE)


@register.filter(is_safe=True)
def csp_nonce_scripts(html, nonce):
    """Gives the response's CSP nonce to the inline <script> elements of HTML
    the site administrators wrote (custom <head>, header and footer), so their
    analytics snippets keep running. Never use it on content members write."""
    if not nonce or html is None:
        return html
    return _SCRIPT_WITHOUT_NONCE.sub('<script nonce="%s"' % nonce, str(html))
