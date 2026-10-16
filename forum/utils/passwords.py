"""Password rules (AUTH_PASSWORD_VALIDATORS in settings.py), following NIST
SP 800-63B: a minimum length (the PASSWORD_MIN_LENGTH setting, never below 8),
a generous maximum, no composition rules, no expiry, and screening against
common and, optionally (PASSWORD_BREACH_CHECK), breached passwords.

The breach check uses the Have I Been Pwned range API with k-anonymity: only
the first 5 hex characters of the password's SHA-1 are sent, and the answer is
padded; it fails open (accepts the password) when the service can't be reached.
"""
import hashlib
import logging
import urllib.request

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

NIST_MIN_LENGTH = 8
MAX_LENGTH = 128
PWNED_RANGE_URL = 'https://api.pwnedpasswords.com/range/%s'
PWNED_TIMEOUT = 3

log = logging.getLogger('forum.security')


def min_length():
    from forum import settings
    try:
        return max(NIST_MIN_LENGTH, int(settings.PASSWORD_MIN_LENGTH))
    except (TypeError, ValueError):
        return NIST_MIN_LENGTH


class MinimumLengthValidator(password_validation.MinimumLengthValidator):
    """Django's validator with the length from the PASSWORD_MIN_LENGTH setting."""

    def __init__(self, min_length=None):
        super().__init__(NIST_MIN_LENGTH)

    @property
    def min_length(self):
        return min_length()

    @min_length.setter
    def min_length(self, value):
        pass  # always read from the setting


class MaximumLengthValidator:
    """Refuses absurdly long passwords (hashing cost); 128 characters is well
    above NIST's 64 and what password managers make."""

    def __init__(self, max_length=MAX_LENGTH):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(
                ngettext("This password is too long. It must contain at most %(max_length)d character.",
                         "This password is too long. It must contain at most %(max_length)d characters.",
                         self.max_length) % {'max_length': self.max_length},
                code='password_too_long', params={'max_length': self.max_length})

    def get_help_text(self):
        return _("At most %(max_length)d characters") % {'max_length': self.max_length}


def breach_count(password, opener=None):
    """How often `password` appears in the Have I Been Pwned list, or None
    when the service could not be asked."""
    digest = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()  # noqa: S324 (the API's format)
    prefix, suffix = digest[:5], digest[5:]
    request = urllib.request.Request(PWNED_RANGE_URL % prefix,
                                     headers={'Add-Padding': 'true', 'User-Agent': 'OSQA password check'})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=PWNED_TIMEOUT) as response:
            body = response.read().decode('ascii', 'replace')
    except Exception as e:  # network, timeout, HTTP error: accept the password
        log.warning('event=password_breach_check_unavailable error=%r', type(e).__name__)
        return None
    for line in body.splitlines():
        hash_suffix, _sep, count = line.strip().partition(':')
        if hash_suffix.upper() == suffix:
            try:
                return int(count)
            except ValueError:
                return 0
    return 0


class BreachedPasswordValidator:
    """Refuses passwords found in data breaches, when PASSWORD_BREACH_CHECK is on."""

    def validate(self, password, user=None):
        from forum import settings
        if not bool(settings.PASSWORD_BREACH_CHECK):
            return
        if breach_count(password):
            raise ValidationError(
                _("This password has appeared in a data breach, so attackers try it first. Please choose another "
                  "one."), code='password_breached')

    def get_help_text(self):
        return _("Not found in known data breaches")


def breach_check_enabled():
    from forum import settings
    return bool(settings.PASSWORD_BREACH_CHECK) and any(
        isinstance(v, BreachedPasswordValidator) for v in password_validation.get_default_password_validators())
