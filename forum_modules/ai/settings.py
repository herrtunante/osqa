from django.forms.widgets import PasswordInput
from django.utils.translation import gettext_lazy as _

from forum.settings.base import Setting, SettingSet
from forum.settings.extkeys import EXT_KEYS_SET

AI_SET = SettingSet('ai', _('AI assistance'), _(
    "Duplicate detection and spam moderation with Claude. Enabled features send the text of new public "
    "posts (titles, bodies, comments) to Anthropic's API; nothing else about the author is sent."), 2000)

ANTHROPIC_API_KEY = Setting('ANTHROPIC_API_KEY', '', EXT_KEYS_SET, dict(
label = _("Anthropic API key"),
help_text = _("Turns on the AI features (Admin > AI assistance). Get a key at "
              "<a href='https://console.anthropic.com/'>console.anthropic.com</a>. If empty, the "
              "ANTHROPIC_API_KEY environment variable of the server is used."),
widget=PasswordInput(render_value=True),
required=False))

AI_MODEL = Setting('AI_MODEL', 'claude-opus-5-5', AI_SET, dict(
label = _("Claude model"),
help_text = _("The model ID used for all AI features, e.g. claude-opus-5-5, or claude-haiku-5-5 for lower cost.")))

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
