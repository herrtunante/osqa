# -*- coding: utf-8 -*-
import json
import logging

from datetime import datetime

from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseNotAllowed
from django.shortcuts import render as django_render
from django.urls import reverse
from django.utils.translation import gettext as _

from forum.modules import ui, decorate
from forum.settings import ONLINE_USERS

def login_required(func, request, *args, **kwargs):
    if not request.user.is_authenticated:
        return HttpResponseRedirect(reverse('auth_signin'))
    else:
        return func(request, *args, **kwargs)

def render(template=None, tab=None, tab_title='', weight=500, tabbed=True):
    def decorator(func):
        def decorated(context, request, *args, **kwargs):
            if request.user.is_authenticated:
                ONLINE_USERS[request.user] = datetime.now()

            if isinstance(context, HttpResponse):
                return context

            if tab is not None:
                context['tab'] = tab

            return django_render(request, context.pop('template', template), context)

        if tabbed and tab and tab_title:
            ui.register(ui.PAGE_TOP_TABS,
                        ui.PageTab(tab, tab_title, lambda: reverse(func.__name__), weight=weight))
            
        return decorate.result.withfn(decorated, needs_params=True)(func)

    return decorator

class CommandException(Exception):
    pass

class RefreshPageCommand(HttpResponse):
    def __init__(self):
        super(RefreshPageCommand, self).__init__(
                content=json.dumps({'commands': {'refresh_page': []}, 'success': True}),
                content_type="application/json")

def prompt(func):
    """Mark a command view that may be requested with GET.

    Such a view must only render its prompt (a form or read-only data) on GET
    and act on POST. Every other command view rejects non-POST requests.
    """
    func.allows_get = True
    return func

def command(func, request, *args, **kwargs):
    # Commands change state, so a plain link or <img> on another page must not
    # trigger them; POST also gets CSRF protection.
    if request.method != 'POST' and not getattr(func, 'allows_get', False):
        return HttpResponseNotAllowed(['POST'])

    try:
        response = func(request, *args, **kwargs)

        if isinstance(response, HttpResponse):
            return response

        response['success'] = True
    except Exception as e:
        import traceback
        #traceback.print_exc()

        if isinstance(e, Http404):
            raise  # a missing post is a 404, not an "unknown error"
        if isinstance(e, CommandException):
            response = {
            'success': False,
            'error_message': str(e)
            }
        else:
            logging.error("%s: %s" % (func.__name__, str(e)))
            logging.error(traceback.format_exc())
            response = {
            'success': False,
            'error_message': _("We're sorry, but an unknown error ocurred.<br />Please try again in a while.")
            }

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return HttpResponse(json.dumps(response), content_type="application/json")
    else:
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

