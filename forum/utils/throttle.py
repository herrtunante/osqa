"""Counters for rate limits (sign-in failures, sign-in link requests).

Counts live in the shared cache for a fixed window that starts at the first
hit, so they need no table and expire by themselves.
"""
import hashlib

from django.core.cache import cache


def _key(name, value):
    digest = hashlib.sha256(str(value).lower().encode('utf-8')).hexdigest()[:32]
    return 'throttle:%s:%s' % (name, digest)


def count(name, value):
    return cache.get(_key(name, value), 0)


def hit(name, value, seconds):
    """Adds one to the counter and returns the new count."""
    key = _key(name, value)
    if cache.add(key, 1, seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired in between
        cache.set(key, 1, seconds)
        return 1


def reset(name, value):
    cache.delete(_key(name, value))


def client_ip(request):
    return request.META.get('REMOTE_ADDR', '')
