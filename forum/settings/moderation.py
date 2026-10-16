from .base import Setting, SettingSet
from .forms import StringListWidget

from django.utils.translation import gettext_lazy as _
from django.forms.widgets import RadioSelect

MODERATION_SET = SettingSet('moderation', _('Moderation settings'), _("Define the moderation workflow of your site"), 100)

USE_CANNED_COMMENTS = Setting('USE_CANNED_COMMENTS', True, MODERATION_SET, dict(
label = _("Use canned comments"),
help_text = _("If you check, the canned comments feature will be activated, allowing moderators to use canned patterns for their comments."),
required=False))

CANNED_COMMENTS = Setting('CANNED_COMMENTS',
[
"We require all postings to have a clear, specific QUESTION in the title field. Please edit this right away to rephrase the title as a proper question.",
],
MODERATION_SET, dict(
label = _("Canned comments"),
help_text = _("""
<p>Create some canned comments to be used for easier moderation. You can access the {{ post }} and {{ settings }} variables.</p>
<p>If you want to access the current post author username you can use {{ post.author }}, for the question title use {{ post.title }}.</p>
<p>To get the application title use {{ settings.APP_TITLE }}. All settings are accessible through the settings object.</p>
"""),
widget=StringListWidget))

FLAG_TYPES = Setting('FLAG_TYPES',
["Spam", "Advertising", "Offensive, Abusive, or Inappropriate", "Content violates terms of use", "Copyright Violation",
 "Misleading", "Someone is not being nice", "Not relevant/off-topic", "Other"],
MODERATION_SET, dict(
label = _("Flag Reasons"),
help_text = _("Create some flag reasons to use in the flag post popup."),
widget=StringListWidget))


CLOSE_TYPES = Setting('CLOSE_TYPES',
["Duplicate Question", "Question is off-topic or not relevant", "Too subjective and argumentative",
 "The question is answered, right answer was accepted", "Problem is not reproducible or outdated", "Other"],
MODERATION_SET, dict(
label = _("Close Reasons"),
help_text = _("Create some close reasons to use in the close question popup."),
widget=StringListWidget))


# Flag control position
REPORT_OFFENSIVE_CONTROL_POSITION_CHOICES = (
    ('more', _('Position it in the "More" menu')),
    ('controls', _('Position it in main controls')),
)

REPORT_OFFENSIVE_CONTROL_POSITION = Setting('REPORT_OFFENSIVE_CONTROL_POSITION', 'more', MODERATION_SET, dict(
label = _("Position of report control"),
help_text = _("Choose the position of the \"report (0)\" control of nodes."),
widget=RadioSelect,
choices=REPORT_OFFENSIVE_CONTROL_POSITION_CHOICES,
required=False))


# First posts review queue (forum/utils/review.py, Admin > Moderation inbox).
FIRST_POSTS_MODERATION_CHOICES = (
    ('off', _("Off: new members' posts are published without review")),
    ('after', _("Review after publishing: posts appear at once and are listed in the moderation inbox")),
    ('before', _("Review before publishing: posts stay hidden (except to their author and moderators) "
                 "until a moderator approves them")),
)

FIRST_POSTS_MODERATION = Setting('FIRST_POSTS_MODERATION', 'off', MODERATION_SET, dict(
label = _("Review new members' first posts"),
help_text = _("Questions, answers and comments by new members go to the moderation inbox (shown to moderators in "
              "the header). Moderators and administrators are never reviewed. With \"before\", other people only see "
              "a post once it is approved, and followers are notified then; the author sees it marked \"Waiting for "
              "review\" and gets an email and a notification when it is approved or removed."),
widget=RadioSelect,
choices=FIRST_POSTS_MODERATION_CHOICES,
required=False))

FIRST_POSTS_REVIEW_COUNT = Setting('FIRST_POSTS_REVIEW_COUNT', 1, MODERATION_SET, dict(
label = _("Posts to review per member"),
help_text = _("A member's posts are reviewed until a moderator has approved this many of them (deleted posts don't "
              "count). 1 reviews only the first post. 0 reviews no one by count (see the reputation below).")))

FIRST_POSTS_REVIEW_MIN_REP = Setting('FIRST_POSTS_REVIEW_MIN_REP', 0, MODERATION_SET, dict(
label = _("Also review posts by members below this reputation"),
help_text = _("Every post by a member with less reputation than this is reviewed, however many posts they have. "
              "0 turns this off.")))


# Suggested edits (forum/utils/suggested_edits.py): members below the
# reputation to edit others' posts may suggest edits, reviewed by those above it.
SUGGESTED_EDITS_ENABLED = Setting('SUGGESTED_EDITS_ENABLED', True, MODERATION_SET, dict(
label = _("Suggested edits"),
help_text = _("Members who can't edit others' posts yet (see the minimum reputation to edit others posts) may suggest "
              "an edit instead. Members who can edit others' posts and moderators review the suggestions."),
required=False))

SUGGESTED_EDITS_PER_DAY = Setting('SUGGESTED_EDITS_PER_DAY', 5, MODERATION_SET, dict(
label = _("Suggested edits per member per day"),
help_text = _("How many edits a member may suggest in 24 hours. 0 for no limit.")))
