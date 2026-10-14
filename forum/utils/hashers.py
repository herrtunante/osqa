"""Password hashes from the original OSQA.

Accounts created on old Django versions (before 1.4) store salted SHA1
hashes ("sha1$salt$hash"), which Django 5.1 stopped reading. This hasher
only checks them: at the next sign-in Django re-hashes the password with the
first hasher in PASSWORD_HASHERS (PBKDF2), so no SHA1 hash is ever written.
"""
import hashlib

from django.contrib.auth.hashers import BasePasswordHasher, mask_hash
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext_noop as _


class LegacySHA1PasswordHasher(BasePasswordHasher):
    algorithm = 'sha1'

    def encode(self, password, salt):
        self._check_encode_args(password, salt)
        return '%s$%s$%s' % (self.algorithm, salt, hashlib.sha1((salt + password).encode()).hexdigest())

    def decode(self, encoded):
        algorithm, salt, hash = encoded.split('$', 2)
        return {'algorithm': algorithm, 'hash': hash, 'salt': salt}

    def verify(self, password, encoded):
        return constant_time_compare(encoded, self.encode(password, self.decode(encoded)['salt']))

    def safe_summary(self, encoded):
        decoded = self.decode(encoded)
        return {_('algorithm'): decoded['algorithm'], _('salt'): mask_hash(decoded['salt'], show=2),
                _('hash'): mask_hash(decoded['hash'])}

    def must_update(self, encoded):
        return True

    def harden_runtime(self, password, encoded):
        pass
