from django.forms.widgets import Textarea
from django.utils.translation import gettext_lazy as _

from .base import Setting, SettingSet

API_SET = SettingSet('api', _('Public API'), _("The JSON API at /api/v1/ (documented at /api/): who may use it, "
                     "how often, and from which web sites."), 27)

API_ENABLED = Setting('API_ENABLED', True, API_SET, dict(
label = _("Turn on the API"),
help_text = _("Programs can read public questions, answers, tags, members and badges, and members can create "
              "personal access tokens. When off, every API address answers 503 and the token page is hidden."),
required=False))

API_WRITES_ENABLED = Setting('API_WRITES_ENABLED', True, API_SET, dict(
label = _("Allow posting through the API"),
help_text = _("Tokens with the write scope may ask, answer, comment, vote, accept answers and follow questions, "
              "with the same reputation rules, posting limits and review queue as the web site. When off, only "
              "reading works."),
required=False))

API_RATE_LIMIT_ANONYMOUS = Setting('API_RATE_LIMIT_ANONYMOUS', 60, API_SET, dict(
label = _("Requests per minute without a token"),
help_text = _("Per IP address. 0 means no limit.")))

API_RATE_LIMIT_TOKEN = Setting('API_RATE_LIMIT_TOKEN', 300, API_SET, dict(
label = _("Requests per minute with a token"),
help_text = _("Per token. 0 means no limit.")))

API_WRITE_RATE_LIMIT = Setting('API_WRITE_RATE_LIMIT', 30, API_SET, dict(
label = _("Changes per minute with a token"),
help_text = _("Posts, votes, accepts and follows per token, on top of the hourly posting limits in the security "
              "settings. 0 means no limit.")))

API_CORS_ORIGINS = Setting('API_CORS_ORIGINS', '*', API_SET, dict(
label = _("Web sites that may read the API from a browser"),
help_text = _("Origins allowed to read the API with JavaScript (CORS), separated by spaces, e.g. "
              "https://example.org. * allows every site; empty allows none. Only reading is allowed this way, "
              "and never with cookies."),
widget=Textarea(attrs={'rows': '2'}),
required=False))
