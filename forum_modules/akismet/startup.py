import json
import logging

from django.utils.translation import gettext as _, gettext_lazy
from django.http import HttpResponse
from django.shortcuts import render
from forum.modules import decorate
from forum import views
from forum.settings import APP_URL, OSQA_VERSION

from forum.forms.general import SimpleCaptchaForm

from .lib.akismet import Akismet, AkismetError
from .settings import WORDPRESS_API_KEY, REP_FOR_NO_SPAM_CHECK

def can_bypass_spam_check(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff or int(user.reputation) > int(REP_FOR_NO_SPAM_CHECK))


def is_spam(request, comment, comment_type):
    data = {
    "user_ip": request.META.get("REMOTE_ADDR", ""),
    "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    "referrer": request.META.get("HTTP_REFERER", ""),
    "comment_type": comment_type,
    }

    if request.user.is_authenticated:
        data.update({
        "comment_author": request.user.username,
        "comment_author_email": request.user.email,
        "comment_author_url": request.user.website,
        })

    api = Akismet(str(WORDPRESS_API_KEY).strip(), APP_URL, "OSQA/%s" % OSQA_VERSION)
    try:
        return api.comment_check(comment, data)
    except AkismetError as e:
        # Don't stop people from posting when Akismet is down or misconfigured.
        logging.warning("Akismet spam check failed, accepting the post: %s", e)
        return False


def check_spam(param, comment_type, action_name):
    """comment_type is the Akismet content type; action_name is shown to the user."""
    def wrapper(origin, request, *args, **kwargs):
        if request.method == 'POST' and request.POST.get(param, None) and str(WORDPRESS_API_KEY).strip() \
                and (not can_bypass_spam_check(request.user)):

            if is_spam(request, request.POST[param], comment_type):
                post_data = request.POST
                captcha_form = SimpleCaptchaForm(request.POST)

                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    response = {
                    'success': False,
                    'error_message': _("Sorry, but akismet thinks your %s is spam.") % action_name
                    }
                    return HttpResponse(json.dumps(response), content_type="application/json")
                else:
                    captcha_checked = False
                    try:
                        if 'recaptcha' in captcha_form.fields.keys() and captcha_form.is_valid():
                            captcha_checked = True
                    except Exception:
                        logging.exception("Error checking the captcha of a post Akismet marked as spam")

                    if not captcha_checked:
                        return render(request, 'modules/akismet/foundspam.html', {
                        'action_name': action_name,
                        'post_data' : post_data,
                        'captcha_form' : captcha_form,
                        })

        return origin(request, *args, **kwargs)

    return wrapper


decorate(views.writers.ask)(check_spam('text', 'forum-post', gettext_lazy('question')))
decorate(views.writers.answer)(check_spam('text', 'reply', gettext_lazy('answer')))
decorate(views.commands.comment)(check_spam('comment', 'comment', gettext_lazy('comment')))
