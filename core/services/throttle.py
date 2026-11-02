"""Login throttling (per account+IP and per IP) using the shared cache; counters expire on their own."""
from __future__ import annotations

import hashlib

from django.core.cache import cache

from core import conf


def _key(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(p.lower() for p in parts).encode()).hexdigest()[:32]     # never store raw emails in cache keys
    return f"core:throttle:{kind}:{digest}"


def is_locked(email: str, ip: str) -> bool:
    limit = conf.get("CORE_LOGIN_MAX_ATTEMPTS")
    return (cache.get(_key("acct", email, ip), 0) >= limit) or (cache.get(_key("ip", ip), 0) >= limit * 4)


def register_failure(email: str, ip: str) -> int:
    window = conf.get("CORE_LOGIN_LOCKOUT_SECONDS")
    count = 0
    for key in (_key("acct", email, ip), _key("ip", ip)):
        cache.add(key, 0, window)
        try:
            value = cache.incr(key)
        except ValueError:
            cache.set(key, 1, window)
            value = 1
        count = max(count, value)
    return count


def reset(email: str, ip: str) -> None:
    cache.delete(_key("acct", email, ip))
