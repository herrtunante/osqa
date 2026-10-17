"""Personal access tokens for the public JSON API (forum/api/).

A member creates a token on /account/<id>/api-tokens/ and sends it as
`Authorization: Bearer <token>`. The token itself is shown once and never
stored: the row keeps its SHA-256 digest (the token has 256 random bits, so a
plain hash can't be reversed or guessed) and its first characters, so the
member can tell tokens apart. Revoking a token deletes the row.
"""
import datetime
import hashlib
import secrets

from django.db import models

from .user import User

TOKEN_PREFIX = 'osqa_'
SHOWN_PREFIX_LENGTH = len(TOKEN_PREFIX) + 6
SCOPES = ('read', 'write')
MAX_TOKENS_PER_USER = 20
# The last use is written at most this often (seconds), not on every request.
LAST_USED_PRECISION = 60


def token_digest(raw):
    return hashlib.sha256(str(raw).encode('utf-8')).hexdigest()


class ApiTokenManager(models.Manager):
    def create_token(self, user, name, scopes=('read',), days=None):
        """A new token for `user`: returns (ApiToken, the raw token to show once)."""
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = datetime.datetime.now()
        token = self.create(user=user, name=name[:64], prefix=raw[:SHOWN_PREFIX_LENGTH], token_hash=token_digest(raw),
                            scopes=' '.join(s for s in SCOPES if s in scopes) or 'read', created=now,
                            expires=now + datetime.timedelta(days=days) if days else None)
        return token, raw

    def find(self, raw):
        """The token whose digest matches `raw`, or None (expired ones too: the caller says why)."""
        if not raw or not str(raw).startswith(TOKEN_PREFIX) or len(raw) > 100:
            return None
        return self.select_related('user').filter(token_hash=token_digest(raw)).first()


class ApiToken(models.Model):
    user = models.ForeignKey(User, related_name='api_tokens', on_delete=models.CASCADE)
    name = models.CharField(max_length=64)
    # The first characters of the token ("osqa_AbC123"), shown in the list.
    prefix = models.CharField(max_length=16)
    token_hash = models.CharField(max_length=64, unique=True)
    # "read" or "read write".
    scopes = models.CharField(max_length=32, default='read')
    created = models.DateTimeField(default=datetime.datetime.now)
    expires = models.DateTimeField(null=True, blank=True)
    last_used = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=39, blank=True)

    objects = ApiTokenManager()

    class Meta:
        app_label = 'forum'
        ordering = ('-created', '-id')

    def __str__(self):
        return '%s: %s (%s)' % (self.user_id, self.name, self.prefix)

    @property
    def scope_list(self):
        return [s for s in self.scopes.split() if s in SCOPES]

    def has_scope(self, scope):
        return scope in self.scope_list

    @property
    def is_expired(self):
        return self.expires is not None and self.expires <= datetime.datetime.now()

    def touch(self, ip):
        """Records a use (at most once a minute, without loading the row again)."""
        now = datetime.datetime.now()
        if self.last_used and (now - self.last_used).total_seconds() < LAST_USED_PRECISION and \
                self.last_used_ip == ip:
            return
        self.last_used, self.last_used_ip = now, ip[:39]
        ApiToken.objects.filter(pk=self.pk).update(last_used=now, last_used_ip=self.last_used_ip)
