"""Passkeys (WebAuthn) with py_webauthn: the options the browser needs and the
checks of what it answers.

- The relying party id is the host of APP_URL and the expected origin its
  scheme://host[:port], so the forum must be reached at APP_URL.
- Each ceremony's challenge is kept in the session (base64url text, the
  purpose, the user for registrations and the time): it is removed when the
  answer arrives (single use) and refused after CHALLENGE_MAX_AGE seconds.
- User verification (fingerprint, face, PIN) is required, so a passkey
  sign-in counts as two factors.
- The signature counter must grow when the authenticator keeps one (a
  counter going back suggests a cloned authenticator): such sign-ins are
  refused and written to the security log.
"""
import datetime
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlparse

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from forum import settings
from forum.utils import security_log

CHALLENGE_SESSION_KEY = 'passkey_challenge'
CHALLENGE_MAX_AGE = 5 * 60
TIMEOUT_MS = 120000
MAX_PASSKEYS = 20


class PasskeyError(Exception):
    pass


def rp_id():
    return urlparse(settings.APP_URL).hostname or 'localhost'


def expected_origin():
    parts = urlparse(settings.APP_URL)
    return '%s://%s' % (parts.scheme, parts.netloc)


def credential_hash(credential_id):
    return hashlib.sha256(credential_id.encode('ascii')).hexdigest()


def _store_challenge(request, purpose, challenge, user=None, **extra):
    request.session[CHALLENGE_SESSION_KEY] = dict(
        extra, purpose=purpose, challenge=bytes_to_base64url(challenge), time=time.time(),
        user=user.pk if user is not None else None)


def _take_challenge(request, purpose, user=None):
    data = request.session.pop(CHALLENGE_SESSION_KEY, None)
    if not isinstance(data, dict) or data.get('purpose') != purpose:
        return None
    if time.time() - float(data.get('time') or 0) > CHALLENGE_MAX_AGE:
        return None
    if data.get('user') != (user.pk if user is not None else None):
        return None
    return dict(data, challenge=base64url_to_bytes(data['challenge']))


def _json(options):
    return json.loads(options_to_json(options))


def _user_handle(user):
    existing = user.passkeys.values_list('user_handle', flat=True).first()
    return existing or bytes_to_base64url(secrets.token_bytes(32))


def registration_options(request, user):
    handle = _user_handle(user)
    options = generate_registration_options(
        rp_id=rp_id(), rp_name=str(settings.APP_SHORT_NAME), user_name=user.username,
        user_id=base64url_to_bytes(handle), user_display_name=user.real_name or user.username,
        timeout=TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
                             for p in user.passkeys.all()],
    )
    _store_challenge(request, 'register', options.challenge, user, handle=handle)
    return _json(options)


def register(request, user, credential, name):
    """Checks the answer of navigator.credentials.create() and saves the passkey."""
    from forum.models import Passkey
    started = _take_challenge(request, 'register', user)
    if started is None:
        raise PasskeyError('challenge')
    challenge = started['challenge']
    if user.passkeys.count() >= MAX_PASSKEYS:
        raise PasskeyError('too_many')
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=expected_origin(), require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        security_log.event('passkey_register_failed', request, logging.WARNING, reason=str(e)[:200])
        raise PasskeyError('invalid')
    credential_id = bytes_to_base64url(verified.credential_id)
    if Passkey.objects.filter(credential_hash=credential_hash(credential_id)).exists():
        raise PasskeyError('exists')
    transports = (credential.get('response') or {}).get('transports') or []
    passkey = Passkey.objects.create(
        user=user, name=name, credential_id=credential_id, credential_hash=credential_hash(credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key), sign_count=verified.sign_count,
        user_handle=started['handle'], backed_up=bool(verified.credential_backed_up),
        transports=','.join(str(t) for t in transports if isinstance(t, str))[:200])
    security_log.event('passkey_added', request, account=user, passkey=passkey.id)
    return passkey


def authentication_options(request):
    """Options for a discoverable-credential sign-in (no user named first)."""
    options = generate_authentication_options(rp_id=rp_id(), timeout=TIMEOUT_MS,
                                              user_verification=UserVerificationRequirement.REQUIRED)
    _store_challenge(request, 'authenticate', options.challenge)
    return _json(options)


def authenticate(request, credential):
    """The Passkey that signed the answer of navigator.credentials.get()."""
    from forum.models import Passkey
    started = _take_challenge(request, 'authenticate')
    if started is None:
        raise PasskeyError('challenge')
    challenge = started['challenge']
    credential_id = credential.get('id') if isinstance(credential, dict) else None
    if not isinstance(credential_id, str) or len(credential_id) > 2048:
        raise PasskeyError('invalid')
    passkey = Passkey.objects.filter(credential_hash=credential_hash(credential_id)).select_related('user').first()
    if passkey is None:
        security_log.event('passkey_unknown', request, logging.WARNING)
        raise PasskeyError('unknown')
    handle = (credential.get('response') or {}).get('userHandle')
    if handle and handle != passkey.user_handle:
        security_log.event('passkey_failed', request, logging.WARNING, account=passkey.user, reason='user handle')
        raise PasskeyError('invalid')
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=expected_origin(), credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count, require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        reason = str(e)[:200]
        event = 'passkey_sign_count_error' if 'sign count' in reason else 'passkey_failed'
        security_log.event(event, request, logging.WARNING, account=passkey.user, reason=reason)
        raise PasskeyError('invalid')
    Passkey.objects.filter(pk=passkey.pk).update(sign_count=verified.new_sign_count,
                                                 backed_up=bool(verified.credential_backed_up),
                                                 last_used=datetime.datetime.now())
    return passkey
