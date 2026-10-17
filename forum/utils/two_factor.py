"""Two-step verification: authenticator app codes (TOTP, RFC 6238), recovery
codes, and the pending sign-in between the password (or another non-passkey
provider) and the code.

- Secrets are encrypted at rest with Fernet under a key derived (HKDF-SHA256)
  from SECRET_KEY; SECRET_KEY_FALLBACKS still decrypt, so a key rotation keeps
  working as long as the old key stays in the fallbacks. Without it the
  secrets can't be read and members sign in with a recovery code.
- Codes are accepted for the current 30-second step and one step either side,
  and only for a step later than the last accepted one (no replay).
- Recovery codes are 12 random characters (60 bits), stored as salt$SHA-256.
- Wrong codes are counted with forum/utils/throttle.py per account and IP.
- Between the first factor and the code the session holds a signed marker
  (PENDING_SESSION_KEY) with the user id and their session auth hash; the
  person is not signed in until the code is right.
Secrets, codes and markers are never logged.
"""
import base64
import datetime
import hashlib
import hmac
import logging
import secrets
import struct
import time
import urllib.parse

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings as djsettings
from django.core import signing

from forum import settings
from forum.utils import security_log, throttle

STEP = 30
DIGITS = 6
WINDOW = 1
RECOVERY_CODES = 10
RECOVERY_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'  # 32 characters: no l, o, 0, 1
RECOVERY_LENGTH = 12

PENDING_SESSION_KEY = 'twofa_pending'
PENDING_SALT = 'forum.two_factor.pending'
PENDING_MAX_AGE = 10 * 60
# How the current session passed the second factor ('totp', 'recovery', 'passkey').
METHOD_SESSION_KEY = 'twofa_method'
SETUP_MAX_AGE = 30 * 60

OK_TOTP, OK_RECOVERY, WRONG, PAUSED = 'totp', 'recovery', 'wrong', 'paused'


# -- secrets at rest -----------------------------------------------------------

def _fernet_key(secret_key):
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'osqa-two-factor',
                   info=b'totp secrets').derive(str(secret_key).encode('utf-8'))
    return Fernet(base64.urlsafe_b64encode(derived))


def _fernet():
    keys = [djsettings.SECRET_KEY] + list(getattr(djsettings, 'SECRET_KEY_FALLBACKS', None) or [])
    return MultiFernet([_fernet_key(k) for k in keys])


def encrypt_secret(raw):
    return _fernet().encrypt(raw).decode('ascii')


def decrypt_secret(token):
    try:
        return _fernet().decrypt(token.encode('ascii'))
    except (InvalidToken, ValueError, AttributeError):
        return None


# -- TOTP ----------------------------------------------------------------------

def new_secret():
    return secrets.token_bytes(20)


def secret_text(raw):
    """The secret as people type it into an app: base32 in groups of four."""
    text = base64.b32encode(raw).decode('ascii').rstrip('=')
    return ' '.join(text[i:i + 4] for i in range(0, len(text), 4))


def hotp(raw, counter):
    digest = hmac.new(raw, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP)


def totp(raw, now=None):
    return hotp(raw, current_step(now))


def normalize_code(code):
    return ''.join(ch for ch in str(code or '') if not ch.isspace() and ch != '-').lower()


def matching_step(raw, code, last_step=0, now=None):
    """The time step `code` belongs to (now, or one step either side), if it
    is later than `last_step`; else None."""
    code = normalize_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if candidate > last_step and hmac.compare_digest(hotp(raw, candidate), code):
            return candidate
    return None


def provisioning_uri(raw, account):
    issuer = str(settings.APP_SHORT_NAME)
    label = urllib.parse.quote('%s:%s' % (issuer, account))
    query = urllib.parse.urlencode({'secret': secret_text(raw).replace(' ', ''), 'issuer': issuer,
                                    'algorithm': 'SHA1', 'digits': DIGITS, 'period': STEP})
    return 'otpauth://totp/%s?%s' % (label, query)


def qr_svg(text, title=''):
    """An inline SVG QR code, made here (no request to a QR service)."""
    import segno
    return segno.make(text, error='m').svg_inline(scale=1, border=4, dark='#000', light='#fff', omitsize=True,
                                                  svgclass='twofa-qr-code', title=title or None)


# -- devices -------------------------------------------------------------------

def device(user):
    from forum.models import TOTPDevice
    if not getattr(user, 'pk', None):
        return None
    return TOTPDevice.objects.filter(user=user).first()


def is_enabled(user):
    from forum.models import TOTPDevice
    return bool(getattr(user, 'pk', None)) and \
        TOTPDevice.objects.filter(user_id=user.pk, confirmed_at__isnull=False).exists()


def setup_device(user):
    """The not yet confirmed device of `user`, made with a new secret unless
    one was started recently (so reloading the page keeps the QR code)."""
    from forum.models import TOTPDevice
    current = TOTPDevice.objects.filter(user=user).first()
    if current and current.confirmed_at:
        return current
    fresh = current and current.created > datetime.datetime.now() - datetime.timedelta(seconds=SETUP_MAX_AGE) and \
        decrypt_secret(current.secret)
    if fresh:
        return current
    if current:
        current.delete()
    return TOTPDevice.objects.create(user=user, secret=encrypt_secret(new_secret()))


def use_totp(dev, code, now=None):
    """Accepts `code` for `dev` once: the step is saved atomically, so the
    same code sent twice (or by two requests at once) works only once."""
    from forum.models import TOTPDevice
    raw = decrypt_secret(dev.secret)
    if raw is None:
        return False
    step = matching_step(raw, code, dev.last_step, now)
    if step is None:
        return False
    updated = TOTPDevice.objects.filter(pk=dev.pk, last_step__lt=step).update(
        last_step=step, last_used=datetime.datetime.now())
    if updated:
        dev.last_step = step
    return bool(updated)


def disable(user):
    from forum.models import RecoveryCode, TOTPDevice
    TOTPDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


# -- recovery codes ------------------------------------------------------------

def _hash_code(code, salt):
    return '%s$%s' % (salt, hashlib.sha256(('%s:%s' % (salt, code)).encode('utf-8')).hexdigest())


def new_recovery_codes(user):
    """Replaces the user's recovery codes; returns the new ones (shown once)."""
    from forum.models import RecoveryCode
    codes = []
    for _i in range(RECOVERY_CODES):
        raw = ''.join(secrets.choice(RECOVERY_ALPHABET) for _j in range(RECOVERY_LENGTH))
        codes.append('-'.join(raw[i:i + 4] for i in range(0, RECOVERY_LENGTH, 4)))
    RecoveryCode.objects.filter(user=user).delete()
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, code_hash=_hash_code(normalize_code(c), secrets.token_hex(8)))
                                      for c in codes])
    return codes


def recovery_codes_left(user):
    from forum.models import RecoveryCode
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def use_recovery_code(user, code):
    from forum.models import RecoveryCode
    code = normalize_code(code)
    if len(code) != RECOVERY_LENGTH:
        return False
    for row in RecoveryCode.objects.filter(user=user, used_at__isnull=True):
        salt = row.code_hash.split('$', 1)[0]
        if hmac.compare_digest(_hash_code(code, salt), row.code_hash):
            return RecoveryCode.objects.filter(pk=row.pk, used_at__isnull=True).update(
                used_at=datetime.datetime.now()) == 1
    return False


# -- checking codes, with limits -------------------------------------------------

def _limit():
    return int(settings.LOGIN_FAILURES_LIMIT) or 10


def paused(request, user):
    limit = _limit()
    return throttle.count('twofa-user', user.pk) >= limit or \
        throttle.count('twofa-ip', throttle.client_ip(request)) >= limit * 4


def check_code(request, user, code, purpose, recovery=True, dev=None):
    """OK_TOTP / OK_RECOVERY when `code` is a valid app code (or, with
    `recovery`, an unused recovery code) of `user`; WRONG or PAUSED otherwise.
    `dev` is the device to check (default: the user's confirmed one)."""
    if paused(request, user):
        security_log.event('two_factor_paused', request, logging.WARNING, account=user, purpose=purpose)
        return PAUSED
    dev = dev or device(user)
    result = WRONG
    if dev is not None and (dev.confirmed_at or purpose == 'setup') and use_totp(dev, code):
        result = OK_TOTP
    elif recovery and is_enabled(user) and use_recovery_code(user, code):
        result = OK_RECOVERY
        security_log.event('recovery_code_used', request, account=user, purpose=purpose,
                           left=recovery_codes_left(user))
    if result == WRONG:
        count_failure(request, user, purpose)
    else:
        throttle.reset('twofa-user', user.pk)
    return result


def lockout_minutes():
    return max(int(settings.LOGIN_LOCKOUT_MINUTES), 1)


def count_failure(request, user, purpose):
    seconds = lockout_minutes() * 60
    throttle.hit('twofa-user', user.pk, seconds)
    throttle.hit('twofa-ip', throttle.client_ip(request), seconds)
    security_log.event('two_factor_failed', request, logging.WARNING, account=user, purpose=purpose)


# -- the pending sign-in -----------------------------------------------------------

# Changes to make to the account only once the code is right (pending marker 'after').
AFTER_RESET_PASSWORD = 'reset_password'  # the email sign-in link: the password was forgotten


def start_pending(request, user, forward=None, message=None, after=()):
    request.session[PENDING_SESSION_KEY] = signing.dumps(
        {'u': user.pk, 'h': user.get_session_auth_hash(), 'f': forward, 'm': str(message) if message else None,
         'a': list(after)},
        salt=PENDING_SALT)


def pending(request):
    """(user, {'forward', 'message', 'after'}) of the sign-in waiting for a code, or (None, None)."""
    from forum.models import User
    marker = request.session.get(PENDING_SESSION_KEY)
    if not marker:
        return None, None
    try:
        data = signing.loads(marker, salt=PENDING_SALT, max_age=PENDING_MAX_AGE)
        user = User.objects.get(pk=data['u'])
    except (signing.BadSignature, User.DoesNotExist, KeyError, TypeError):
        clear_pending(request)
        return None, None
    if not hmac.compare_digest(user.get_session_auth_hash(), data.get('h') or ''):
        clear_pending(request)
        return None, None
    return user, {'forward': data.get('f'), 'message': data.get('m'), 'after': list(data.get('a') or [])}


def clear_pending(request):
    request.session.pop(PENDING_SESSION_KEY, None)


# -- staff requirement -------------------------------------------------------------

def staff_must_set_up(request):
    """True when the setting requires two-step verification for staff and the
    signed-in moderator/administrator neither has it on nor signed in with a passkey."""
    user = request.user
    if not (user.is_authenticated and (user.is_staff or user.is_superuser)):
        return False
    if not bool(settings.TWO_FACTOR_REQUIRED_FOR_STAFF):
        return False
    return not is_enabled(user) and request.session.get(METHOD_SESSION_KEY) != 'passkey'
