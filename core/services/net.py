from __future__ import annotations

from django.conf import settings


def client_ip(request) -> str:
    """REMOTE_ADDR, or the right-most X-Forwarded-For hop only when CORE_TRUST_PROXY_HEADERS is set (behind your own proxy)."""
    if getattr(settings, "CORE_TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]


def safe_next(request, candidate: str, fallback: str = "/") -> str:
    from django.utils.http import url_has_allowed_host_and_scheme

    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()},
                                                     require_https=request.is_secure()):
        return candidate
    return fallback
