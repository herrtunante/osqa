"""Two-step verification (authenticator app codes and recovery codes) and
passkeys (WebAuthn). The logic is in forum/utils/two_factor.py and
forum/utils/passkeys.py; the pages in forum/views/security.py.

What is stored:
- TOTPDevice.secret: the authenticator app's shared secret, encrypted with
  Fernet (AES-128-CBC + HMAC-SHA256) under a key derived from SECRET_KEY
  (SECRET_KEY_FALLBACKS still decrypt after a key rotation).
- RecoveryCode.code_hash: salt$SHA-256 of a 60-bit random code; the codes
  themselves are shown once and never stored.
- Passkey: the credential's public key and signature counter (nothing secret).
"""
import datetime

from django.db import models

from .user import User


class TOTPDevice(models.Model):
    """A member's authenticator app. Two-step verification is on when
    `confirmed_at` is set (the member typed a code from the app); before that
    the row only holds the secret being set up."""

    user = models.OneToOneField(User, related_name='totp_device', on_delete=models.CASCADE)
    secret = models.TextField()
    created = models.DateTimeField(default=datetime.datetime.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The last time step whose code was accepted: a code is never accepted twice.
    last_step = models.BigIntegerField(default=0)
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'

    def __str__(self):
        return 'TOTP of %s%s' % (self.user_id, '' if self.confirmed_at else ' (not confirmed)')


class RecoveryCode(models.Model):
    """One of the ten one-time codes for signing in without the app."""

    user = models.ForeignKey(User, related_name='recovery_codes', on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=100)
    created = models.DateTimeField(default=datetime.datetime.now)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('id',)

    def __str__(self):
        return 'Recovery code %s of %s' % (self.id, self.user_id)


class Passkey(models.Model):
    """A WebAuthn credential (a passkey in a phone, computer or security key)."""

    user = models.ForeignKey(User, related_name='passkeys', on_delete=models.CASCADE)
    name = models.CharField(max_length=64)
    # base64url; credential ids can be up to 1023 bytes, so the unique index is on a hash.
    credential_id = models.TextField()
    credential_hash = models.CharField(max_length=64, unique=True)
    public_key = models.TextField()
    sign_count = models.PositiveBigIntegerField(default=0)
    # The WebAuthn user handle (random, the same for all of a member's passkeys).
    user_handle = models.CharField(max_length=128)
    transports = models.CharField(max_length=200, blank=True)
    backed_up = models.BooleanField(default=False)
    created = models.DateTimeField(default=datetime.datetime.now)
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('created', 'id')

    def __str__(self):
        return '%s: %s' % (self.user_id, self.name)
