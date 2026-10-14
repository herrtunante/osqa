import json
import os
from itertools import groupby

import django.contrib.admin
from django.shortcuts import render
from django.http import HttpResponseRedirect, HttpResponse
from django.views.static import serve
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.safestring import mark_safe

from django.contrib import messages

from forum.utils.redact import redact_params, redact_meta, REDACTED
from forum import settings
from forum.views.decorators import login_required
from forum.forms import FeedbackForm
from forum.modules import decorate
from forum.forms import get_next_url
from forum.models import Badge, Award, User, Page
from forum.http_responses import HttpResponseNotFound, HttpResponseIntServerError
from forum.utils.mail import send_template_email
from forum.templatetags.extra_filters import or_preview

from . import decorators
import logging, traceback

def favicon(request):
    return HttpResponseRedirect(str(settings.APP_FAVICON))

def custom_css(request):
    return HttpResponse(or_preview(settings.CUSTOM_CSS, request), content_type="text/css")

def static(request, title, content):
    return render(request, 'static.html', {'content' : content, 'title': title})

DJANGO_ADMIN_MEDIA_ROOT = os.path.join(os.path.dirname(django.contrib.admin.__file__), 'static', 'admin')


def media(request, skin, path):
    response = serve(request, "%s/media/%s" % (skin, path),
                 document_root=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'skins').replace('\\', '/'))
    return _with_charset(response)


def django_admin_media(request, path):
    """Django's own admin CSS, JS and icons, used by the django-style admin
    interface. Served like the skin media so no collectstatic/web server
    setup is needed."""
    return _with_charset(serve(request, path, document_root=DJANGO_ADMIN_MEDIA_ROOT))


def _with_charset(response):
    content_type = response.get('Content-Type', '')
    if ('charset=' not in content_type):
        if (content_type.startswith('text') or content_type=='application/x-javascript'):
            content_type += '; charset=utf-8'
            response['Content-Type'] = content_type
    return response


def markdown_help(request):
    return render(request, 'markdown_help.html')

@cache_page(60 * 60 * 24 * 30) #30 days
def opensearch(request):
    return render(request, 'opensearch.html', {'settings' : settings})


def feedback(request):
    if request.method == "POST":
        form = FeedbackForm(request.user, data=request.POST)
        if form.is_valid():
            context = {
                 'user': request.user,
                 'email': request.user.is_authenticated and request.user.email or form.cleaned_data.get('email', None),
                 'message': form.cleaned_data['message'],
                 'name': request.user.is_authenticated and request.user.username or form.cleaned_data.get('name', None),
                 'ip': request.META['REMOTE_ADDR'],
            }

            recipients = User.objects.filter(is_superuser=True)
            send_template_email(recipients, "notifications/feedback.html", context)

            msg = _('Thanks for the feedback!')
            messages.info(request, msg)
            return HttpResponseRedirect(get_next_url(request))
    else:
        form = FeedbackForm(request.user, initial={'next':get_next_url(request)})

    return render(request, 'feedback.html', {'form': form})

feedback.CANCEL_MESSAGE=_lazy('We look forward to hearing your feedback! Please, give it next time :)')

def privacy(request):
    return render(request, 'privacy.html')

@decorate.withfn(login_required)
def logout(request):
    return render(request, 'logout.html', {
    'next' : get_next_url(request),
    })

@decorators.render('badges.html', 'badges', _lazy('badges'), weight=300)
def badges(request):
    from forum.badges.base import BadgesMeta
    BadgesMeta.install()
    badges = sorted(Badge.objects.filter(cls__in=list(BadgesMeta.by_class)), key=lambda b: b.name)

    if request.user.is_authenticated:
        my_badges = Award.objects.filter(user=request.user).values_list('badge_id', flat=True).distinct()
    else:
        my_badges = []

    return {
        'badges' : badges,
        'mybadges' : my_badges,
    }

def badge(request, id, slug):
    badge = Badge.objects.get(id=id)
    awards = list(Award.objects.filter(badge=badge).order_by('user', 'awarded_at'))
    award_count = len(awards)

    awards = sorted([dict(count=len(list(g)), user=k) for k, g in groupby(awards, lambda a: a.user)],
                    key=lambda c: c['count'], reverse=True)

    return render(request, 'badge.html', {
    'award_count': award_count,
    'awards' : awards,
    'badge' : badge,
    })

def page(request, exception=None):
    path = request.path[1:]

    if path in settings.STATIC_PAGE_REGISTRY:
        try:
            page = Page.objects.get(id=settings.STATIC_PAGE_REGISTRY[path])

            if (not page.published) and (not request.user.is_superuser):
                return HttpResponseNotFound(request)
        except:
            return HttpResponseNotFound(request)
    else:
        return HttpResponseNotFound(request)

    template = page.extra.get('template', 'default')
    sidebar = page.extra.get('sidebar', '')

    if template == 'default':
        base = 'base_content.html'
    elif template == 'sidebar':
        base = 'base.html'

        sidebar_render = page.extra.get('render', 'markdown')

        if sidebar_render == 'markdown':
            sidebar = page._as_markdown(sidebar)
        elif sidebar_render == 'html':
            sidebar = mark_safe(sidebar)

    else:
        return HttpResponse(page.body, content_type=page.extra.get('mimetype', 'text/html'))

    body_render = page.extra.get('render', 'markdown')

    if body_render == 'markdown':
        body = page.as_markdown()
    elif body_render == 'html':
        body = mark_safe(page.body)
    else:
        body = page.body

    return render(request, 'page.html', {
    'page' : page,
    'body' : body,
    'sidebar': sidebar,
    'base': base,
    })


def error_handler(request):

    stacktrace = "".join(["\t\t%s\n" % l for l in traceback.format_exc().split("\n")])

    try:
        log_msg = """
        error executing request:
        PATH: %(path)s
        USER: %(user)s
        METHOD: %(method)s
        POST PARAMETERS:
        %(post)s
        GET PARAMETERS:
        %(get)s
        HTTP HEADERS:
        %(headers)s
        COOKIES:
        %(cookies)s
        EXCEPTION INFO:
        %(stacktrace)s
        """ % {
            'path': request.path,
            'user': request.user.is_authenticated and ("%s (%s)" % (request.user.username, request.user.id)) or "<anonymous>",
            'method': request.method,
            'post': request.POST and "".join(["\t\t%s: %s\n" % (k, v) for k, v in redact_params(request.POST)]) or "None",
            'get': request.GET and "".join(["\t\t%s: %s\n" % (k, v) for k, v in redact_params(request.GET)]) or "None",
            # Cookie values (session id, CSRF token) would let a log reader hijack the session.
            'cookies': request.COOKIES and "".join(["\t\t%s: %s\n" % (k, REDACTED) for k in request.COOKIES]) or "None",
            'headers': request.META and "".join(["\t\t%s: %s\n" % (k, v) for k, v in redact_meta(request.META)]) or "None",
            'stacktrace': stacktrace
        }
    except:
        log_msg = "error executing request:\n%s" % stacktrace


    logging.getLogger('forum.errors').error(log_msg)
    return HttpResponseIntServerError(request)


@csrf_exempt
@require_POST
def csp_report(request):
    """Browsers post here what the Content-Security-Policy blocked; it goes to
    the security log so admins can see what to add to CSP_EXTRA_SOURCES."""
    from forum.utils import security_log, throttle
    if throttle.hit('csp-report', throttle.client_ip(request), 3600) <= 20:
        try:
            report = json.loads(request.body[:8192]).get('csp-report', {})
        except (ValueError, AttributeError):
            report = {}
        if isinstance(report, dict):
            directive = str(report.get('violated-directive', ''))[:100]
            blocked = str(report.get('blocked-uri', ''))[:200]
            page = str(report.get('document-uri', ''))[:200]
            security_log.event('csp_blocked', request, logging.WARNING, directive=directive, blocked=blocked, page=page)
            from forum.utils.sentry import capture_csp_violation
            capture_csp_violation(directive, blocked, page)
    return HttpResponse(status=204)
