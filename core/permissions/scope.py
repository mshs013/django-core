"""Company membership: which companies may a user act in. Fail closed -- no profile, inactive user or no membership => empty set."""
from __future__ import annotations

from . import cache


def user_company_ids(user) -> frozenset:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    cached = cache.get("companies", user.pk)
    if cached is not None:
        return cached
    from core.models import Company, Profile

    profile = Profile.objects.filter(user_id=user.pk).first()
    ids = frozenset()
    if profile is not None:
        ids = frozenset(profile.companies.filter(deleted_at__isnull=True, is_active=True).values_list("pk", flat=True))
    cache.put(ids, "companies", user.pk)
    return ids


def active_company_id(user):
    """The user's active company if it is still one of their memberships, else the default, else None."""
    ids = user_company_ids(user)
    if not ids:
        return None
    from core.models import Profile

    profile = Profile.objects.filter(user_id=user.pk).only("active_company_id", "default_company_id").first()
    if profile is None:
        return None
    for candidate in (profile.active_company_id, profile.default_company_id):
        if candidate in ids:
            return candidate
    return None


def scope_queryset(queryset, lookup: str | None, company_ids):
    """Restrict ``queryset`` to ``company_ids`` through ORM path ``lookup`` (``company`` / ``companies`` / ``profile__companies`` ...).
    Fail closed: no lookup on a scoped model is the caller's decision; empty ids always yield an empty queryset."""
    if not lookup:
        return queryset
    ids = [i for i in (company_ids or ()) if i is not None]
    if not ids:
        return queryset.none()
    qs = queryset.filter(**{f"{lookup}__in": ids})
    return qs.distinct() if lookup.endswith("companies") else qs
