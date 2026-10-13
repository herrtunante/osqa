from django.forms.widgets import PasswordInput
from django.utils.translation import gettext_lazy as _

from forum.settings.base import Setting, SettingSet
from forum.settings.extkeys import EXT_KEYS_SET

AI_SET = SettingSet('ai', _('AI assistance'), _(
    "Duplicate detection and spam moderation with Google Gemini. Enabled features send the text of new public "
    "posts (titles, bodies, comments) to Google (the Gemini API, or Vertex AI in your Google Cloud project); "
    "nothing else about the author is sent."), 2000)

GEMINI_API_KEY = Setting('GEMINI_API_KEY', '', EXT_KEYS_SET, dict(
label = _("Gemini API key"),
help_text = _("Turns on the AI features (Admin > AI assistance) through the Gemini API. Get a key at "
              "<a href='https://aistudio.google.com/apikey'>Google AI Studio</a>. Not needed when a Vertex AI "
              "project is set under Admin > AI assistance. If empty, the server's GEMINI_API_KEY or "
              "GOOGLE_API_KEY environment variable is used."),
widget=PasswordInput(render_value=True),
required=False))

AI_VERTEX_PROJECT = Setting('AI_VERTEX_PROJECT', '', AI_SET, dict(
label = _("Vertex AI project"),
help_text = _("Google Cloud project ID to use Gemini through Vertex AI instead of an API key. The server "
              "authenticates with Application Default Credentials (its service account, or "
              "<code>gcloud auth application-default login</code>), which need the Vertex AI User role. "
              "If empty, the GOOGLE_CLOUD_PROJECT environment variable is used."),
required=False))

AI_VERTEX_LOCATION = Setting('AI_VERTEX_LOCATION', 'global', AI_SET, dict(
label = _("Vertex AI location"),
help_text = _("Region of the Vertex AI endpoint, e.g. global, europe-west1 or us-central1."),
required=False))

AI_MODEL = Setting('AI_MODEL', 'gemini-3.8-flash', AI_SET, dict(
label = _("Gemini model"),
help_text = _("The model ID used for all AI features, e.g. gemini-3.8-flash, or gemini-3.5-flash-lite for lower cost.")))

AI_DUPLICATE_DETECTION = Setting('AI_DUPLICATE_DETECTION', True, AI_SET, dict(
label = _("Duplicate detection"),
help_text = _("While someone writes a question title, point out existing questions that ask the same thing."),
required=False))

AI_DUPLICATE_CHECKS_PER_HOUR = Setting('AI_DUPLICATE_CHECKS_PER_HOUR', 30, AI_SET, dict(
label = _("Duplicate checks per visitor per hour"),
help_text = _("Limits API use; beyond it the ask page shows the plain keyword matches.")))

AI_SPAM_MODERATION = Setting('AI_SPAM_MODERATION', True, AI_SET, dict(
label = _("Spam and abuse moderation"),
help_text = _("Review new and edited posts in the background. Suspected spam, abuse or off-topic posts are listed "
              "for moderators under Flagged posts; they are never hidden or deleted automatically."),
required=False))

AI_MODERATION_SKIP_REP = Setting('AI_MODERATION_SKIP_REP', 500, AI_SET, dict(
label = _("Skip moderation from reputation"),
help_text = _("Posts by users with at least this reputation (and by staff) are not reviewed.")))

AI_MODERATION_MIN_CONFIDENCE = Setting('AI_MODERATION_MIN_CONFIDENCE', 0.7, AI_SET, dict(
label = _("Report from confidence"),
help_text = _("How sure (0 to 1) the review must be before a post is reported to moderators.")))
