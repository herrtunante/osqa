from django.utils.translation import gettext_lazy as _

from .base import Setting, SettingSet

SUPPORT_SET = SettingSet('support', _('Support and reminders'), _("The staff support dashboard (Admin > Support "
                         "dashboard) and the reminders to accept answers."), 26)

SUPPORT_TAGS = Setting('SUPPORT_TAGS', '', SUPPORT_SET, dict(
label = _("Product tags"),
help_text = _("Tags shown one per row in the dashboard (e.g. collect collect-earth sepal arena calc), separated by "
              "spaces or commas. Empty: the 8 most used tags."),
required=False))

SUPPORT_RESPONSE_TARGET_HOURS = Setting('SUPPORT_RESPONSE_TARGET_HOURS', 48, SUPPORT_SET, dict(
label = _("Response target (hours)"),
help_text = _("Unanswered questions older than this are highlighted in the dashboard.")))

ACCEPT_REMINDER_DAYS = Setting('ACCEPT_REMINDER_DAYS', 7, SUPPORT_SET, dict(
label = _("Remind askers to accept an answer after (days)"),
help_text = _("manage.py remind_unaccepted (run daily) reminds the asker once, by notification and email, when their "
              "question has had an answer for this many days but none is accepted. Only questions asked in the last "
              "60 days are considered. 0 turns reminders off.")))
