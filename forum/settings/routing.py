from django.utils.translation import gettext_lazy as _

from .base import Setting, SettingSet

ROUTING_SET = SettingSet('routing', _('Questions for experts'), _("Expert routing: a new question is suggested to "
                         "the members who answered questions in its tags best (\"Questions for you\")."), 28)

EXPERT_ROUTING_ENABLED = Setting('EXPERT_ROUTING_ENABLED', True, ROUTING_SET, dict(
label = _("Suggest new questions to experts"),
help_text = _("When a question is published, the members with the most expertise in its tags get a notification "
              "(and an email, as often as they chose). Members can turn this off in their preferences. The "
              "\"Questions for you\" page works either way."),
required=False))

EXPERT_ROUTING_MAX_EXPERTS = Setting('EXPERT_ROUTING_MAX_EXPERTS', 5, ROUTING_SET, dict(
label = _("Experts asked per question"),
help_text = _("At most this many members are asked to answer each new question.")))

EXPERT_ROUTING_MIN_SCORE = Setting('EXPERT_ROUTING_MIN_SCORE', 5, ROUTING_SET, dict(
label = _("Minimum expertise"),
help_text = _("Expertise in a tag: 1 point per answer, 3 more if it was accepted, half a point per vote (up to 10 "
              "votes); answers older than a year count a quarter. Members below this in all of a question's tags "
              "aren't asked. Refreshed daily by manage.py refresh_tag_expertise.")))

EXPERT_ROUTING_DAILY_CAP = Setting('EXPERT_ROUTING_DAILY_CAP', 3, ROUTING_SET, dict(
label = _("Requests per member per day"),
help_text = _("Nobody is asked to answer more than this many questions in 24 hours.")))
