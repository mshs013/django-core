"""Permission/menu cache. One global version number namespaces every key, so a single ``bump()`` invalidates everything on every
worker (the default cache is file based and shared between processes; use Redis or the DB cache in clusters)."""
from __future__ import annotations

from django.core.cache import cache

from core import conf

_VERSION_KEY = "core:perm:version"


def version() -> int:
    value = cache.get(_VERSION_KEY)
    if value is None:
        cache.add(_VERSION_KEY, 1, None)
        value = cache.get(_VERSION_KEY) or 1
    return int(value)


def bump() -> int:
    try:
        return int(cache.incr(_VERSION_KEY))
    except ValueError:
        cache.set(_VERSION_KEY, 2, None)
        return 2


def key(*parts) -> str:
    return "core:perm:%d:%s" % (version(), ":".join(str(p) for p in parts))


def get(*parts):
    return cache.get(key(*parts))


def put(value, *parts, timeout: int | None = None) -> None:
    cache.set(key(*parts), value, conf.get("CORE_PERMISSION_CACHE_SECONDS") if timeout is None else timeout)
