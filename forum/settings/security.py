from django.forms.widgets import Textarea
from django.utils.translation import gettext_lazy as _

from .base import Setting, SettingSet

SECURITY_SET = SettingSet('security', _('Security'), _("Sign-in protection, posting limits and the "
                          "Content-Security-Policy header."), 25)

LOGIN_FAILURES_LIMIT = Setting('LOGIN_FAILURES_LIMIT', 5, SECURITY_SET, dict(
label = _("Failed sign-ins before a pause"),
help_text = _("After this many wrong passwords for one account, signing in to it is paused (the person can still "
              "get a sign-in link by email). An IP address may fail four times as often across all accounts. "
              "0 turns this off.")))

LOGIN_LOCKOUT_MINUTES = Setting('LOGIN_LOCKOUT_MINUTES', 15, SECURITY_SET, dict(
label = _("Sign-in pause (minutes)"),
help_text = _("How long signing in is paused after too many wrong passwords.")))

PASSWORD_RESETS_PER_HOUR = Setting('PASSWORD_RESETS_PER_HOUR', 3, SECURITY_SET, dict(
label = _("Sign-in link emails per hour"),
help_text = _("How many \"forgot password\" sign-in links one email address, or one IP address (four times as "
              "many), may request per hour. 0 means no limit.")))

QUESTIONS_PER_HOUR = Setting('QUESTIONS_PER_HOUR', 10, SECURITY_SET, dict(
label = _("Questions per user per hour"),
help_text = _("Moderators and administrators have no limit. 0 means no limit.")))

ANSWERS_PER_HOUR = Setting('ANSWERS_PER_HOUR', 20, SECURITY_SET, dict(
label = _("Answers per user per hour"),
help_text = _("Moderators and administrators have no limit. 0 means no limit.")))

COMMENTS_PER_HOUR = Setting('COMMENTS_PER_HOUR', 40, SECURITY_SET, dict(
label = _("Comments per user per hour"),
help_text = _("Moderators and administrators have no limit. 0 means no limit.")))

CSP_EXTRA_SOURCES = Setting('CSP_EXTRA_SOURCES', '', SECURITY_SET, dict(
label = _("Extra script sources"),
help_text = _("The Content-Security-Policy header only lets the browser load scripts, styles and frames from this "
              "site and the Google services OSQA uses. If you add analytics or widgets in the custom <head> or "
              "footer, list their origins here, separated by spaces (e.g. https://plausible.io). Blocked items are "
              "written to the security log."),
widget=Textarea(attrs={'rows': '3'}),
required=False))
