"""URL names -> permissions. ``seed_url_permissions`` creates one Permission per named URL (never deletes, never grants)."""
from __future__ import annotations

import re

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import URLPattern, URLResolver, get_resolver

from core import conf

PREFIX = "access_"
MAX_CODENAME = 100


def permission_codename(url_name: str) -> str:
    return (PREFIX + re.sub(r"[^a-z0-9_]+", "_", url_name.lower()).strip("_"))[:MAX_CODENAME]


def collect_url_names() -> tuple[list[str], int]:
    """(sorted unique ``ns:name`` list, number of unnamed patterns)."""
    names: set[str] = set()
    unnamed = 0

    def walk(patterns, namespace: str):
        nonlocal unnamed
        for p in patterns:
            if isinstance(p, URLResolver):
                ns = f"{namespace}{p.namespace}:" if p.namespace else namespace
                walk(p.url_patterns, ns)
            elif isinstance(p, URLPattern):
                if p.name:
                    names.add(f"{namespace}{p.name}")
                else:
                    unnamed += 1

    walk(get_resolver().url_patterns, "")
    return sorted(names), unnamed


def seed(dry_run: bool = False) -> dict:
    """Create missing permissions for protected URL names. Existing rows are untouched; stale ones are only reported."""
    public = set(conf.get("CORE_PUBLIC_URL_NAMES"))
    names, unnamed = collect_url_names()
    protected = [n for n in names if n not in public]
    ct, _ = (ContentType.objects.get_or_create(app_label="core", model="urlaccess") if not dry_run
             else (ContentType.objects.filter(app_label="core", model="urlaccess").first(), False))
    existing = set(Permission.objects.filter(content_type=ct).values_list("codename", flat=True)) if ct else set()
    wanted = {permission_codename(n): n for n in protected}
    missing = sorted(c for c in wanted if c not in existing)
    stale = sorted(existing - set(wanted))
    if not dry_run:
        Permission.objects.bulk_create([Permission(content_type=ct, codename=c, name=f"Can open {wanted[c]}"[:255]) for c in missing])
        from . import cache

        if missing:
            cache.bump()
    return {"created": missing, "stale": stale, "unnamed_patterns": unnamed, "public": sorted(public & set(names)), "dry_run": dry_run}
