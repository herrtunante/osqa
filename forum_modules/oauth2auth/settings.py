from django.utils.translation import gettext_lazy as _

from forum.settings import EXT_KEYS_SET
from forum.settings.base import Setting

# Each provider shows up on the sign-in page once its client id and secret are
# set. Register the redirect URI <APP_URL>/account/<provider>/done/ with the
# provider (provider = google, github or facebook).

GOOGLE_CLIENT_ID = Setting('GOOGLE_CLIENT_ID', '', EXT_KEYS_SET, dict(
label = _("Google client ID"),
help_text = _("OAuth client ID from the Google Cloud console (APIs & Services > Credentials). "
              "Authorized redirect URI: <APP_URL>/account/google/done/"),
required=False))

GOOGLE_CLIENT_SECRET = Setting('GOOGLE_CLIENT_SECRET', '', EXT_KEYS_SET, dict(
label = _("Google client secret"),
required=False))

GITHUB_CLIENT_ID = Setting('GITHUB_CLIENT_ID', '', EXT_KEYS_SET, dict(
label = _("GitHub client ID"),
help_text = _("From a GitHub OAuth app (Settings > Developer settings > OAuth Apps). "
              "Authorization callback URL: <APP_URL>/account/github/done/"),
required=False))

GITHUB_CLIENT_SECRET = Setting('GITHUB_CLIENT_SECRET', '', EXT_KEYS_SET, dict(
label = _("GitHub client secret"),
required=False))

FACEBOOK_APP_ID = Setting('FACEBOOK_APP_ID', '', EXT_KEYS_SET, dict(
label = _("Facebook app ID"),
help_text = _("From developers.facebook.com (Facebook Login product). "
              "Valid OAuth redirect URI: <APP_URL>/account/facebook/done/"),
required=False))

FACEBOOK_APP_SECRET = Setting('FACEBOOK_APP_SECRET', '', EXT_KEYS_SET, dict(
label = _("Facebook app secret"),
required=False))
