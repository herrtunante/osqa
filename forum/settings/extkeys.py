from .base import Setting, SettingSet
from django.utils.translation import gettext_lazy as _

EXT_KEYS_SET = SettingSet('extkeys', _('External Keys'), _("Keys for various external providers that your application may optionally use."), 100)

GOOGLE_SITEMAP_CODE = Setting('GOOGLE_SITEMAP_CODE', '', EXT_KEYS_SET, dict(
label = _("Google sitemap code"),
help_text = _("This is the code you get when you register your site at <a href='https://www.google.com/webmasters/tools/'>Google webmaster central</a>."),
required=False))

GOOGLE_ANALYTICS_KEY = Setting('GOOGLE_ANALYTICS_KEY', '', EXT_KEYS_SET, dict(
label = _("Google analytics key"),
help_text = _("Your Google analytics key. You can get one at the <a href='http://www.google.com/analytics/'>Google analytics official website</a>"),
required=False))


GOOGLE_CSE_ID = Setting('GOOGLE_CSE_ID', '', EXT_KEYS_SET, dict(
label = _("Google custom search engine ID"),
help_text = _("The search engine ID (cx) of a <a href='https://programmablesearchengine.google.com/'>Google Programmable Search Engine</a> to use for the search box in the header. Leave it empty to use the site's own search."),
required=False))
