"""Typed permission service wrapping Django permissions + django-guardian object permissions, company-scope aware.

Rules (fail closed):
1. Anonymous / inactive user -> False.
2. Superuser -> True. This bypass is deliberate, explicit and covered by tests; it applies to permissions only -- company
   membership is still required for company-scoped *data* (see ``scope.user_company_ids``) unless a superuser is acting in no company.
3. ``company`` given -> the user must be a member of it.
4. ``obj`` given and company scoped -> the user must be a member of one of the object's companies.
5. Otherwise ``user.has_perm(perm, obj)`` (global permission OR guardian object permission).

``STAFF_PERM`` (``"core.is_staff"``) is a pseudo-permission: it is granted by the user's ``is_staff`` flag instead of a permission row,
so a menu entry or resource can be opened to "superusers and staff" (the audit log uses it). Staff also hold ``STAFF_IMPLIED``
(users, departments, designations, notification rules, backups, archive, export jobs: the "staff & superuser" admin pages).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.db import models, transaction

from core.models.base import object_company_ids

from . import cache
from .scope import user_company_ids


STAFF_PERM = "core.is_staff"
# Staff hold these without a permission row: the Administration pages open to "staff & superusers" (docs/MENUS.md). The user
# form keeps staff off superuser accounts and flags. Restoring a backup or an archive and deleting backups stay superuser-only.
STAFF_IMPLIED = frozenset({
    "core.view_user", "core.add_user", "core.change_user",
    *(f"core.{action}_{model}" for action in ("view", "add", "change", "delete")
      for model in ("department", "designation", "notificationrule")),
    "core.view_backuprecord", "core.create_backup", "core.download_backup", "core.validate_backup",
    "core.view_archivejob", "core.run_archive",
    "core.view_exportjob", "core.view_jobrecord",
})
# Permission each everyday page needs (menu entry and page alike). Dashboard is the only page open to every signed-in user.
PAGE_PERMS = {"approvals": "core.view_approvalrequest", "reports": "core.view_reportdefinition", "mail": "core.view_mailmessage",
              "notifications": "core.view_notification"}


def _obj_key(obj) -> str:
    if obj is None:
        return "-"
    return f"{obj._meta.label_lower}:{obj.pk}"


def user_has_perm(user, perm: str, obj: models.Model | None = None, company=None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    company_id = getattr(company, "pk", company)
    members = user_company_ids(user)
    if company_id is not None and company_id not in members:
        return False
    if obj is not None:
        owners = object_company_ids(obj)
        if getattr(obj, "company_lookup", None) and not (owners & members):
            return False
    if perm == STAFF_PERM or (perm in STAFF_IMPLIED and user.is_staff):
        return bool(user.is_staff)
    ck = ("has", user.pk, perm, _obj_key(obj), company_id)
    hit = cache.get(*ck)
    if hit is not None:
        return hit
    for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(attr, None)            # Django's per-instance caches would hide grants made after the first check
    # ModelBackend answers False whenever ``obj`` is given, so the global grant is checked on its own (rule 5).
    result = bool(user.has_perm(perm) or (obj is not None and user.has_perm(perm, obj)))
    cache.put(result, *ck)
    return result


def _perm_for(perm: str, obj) -> tuple[str, Any]:
    return perm.split(".", 1)[-1], obj


def assign_perm(perm: str, user_or_group, obj: models.Model | None = None) -> None:
    from guardian.shortcuts import assign_perm as g_assign

    with transaction.atomic():
        g_assign(perm, user_or_group, obj)
    cache.bump()
    _audit_change("assign", perm, user_or_group, obj)


def remove_perm(perm: str, user_or_group, obj: models.Model | None = None) -> None:
    from guardian.shortcuts import remove_perm as g_remove

    with transaction.atomic():
        g_remove(perm, user_or_group, obj)
    cache.bump()
    _audit_change("remove", perm, user_or_group, obj)


def bulk_assign_perm(perm: str, user_or_group, objects: Iterable[models.Model]) -> int:
    """Grant ``perm`` on every object atomically (all or none)."""
    from guardian.shortcuts import assign_perm as g_assign

    objs = list(objects)
    with transaction.atomic():
        for obj in objs:
            g_assign(perm, user_or_group, obj)
    cache.bump()
    _audit_change("assign", perm, user_or_group, None, count=len(objs))
    return len(objs)


def bulk_remove_perm(perm: str, user_or_group, objects: Iterable[models.Model]) -> int:
    from guardian.shortcuts import remove_perm as g_remove

    objs = list(objects)
    with transaction.atomic():
        for obj in objs:
            g_remove(perm, user_or_group, obj)
    cache.bump()
    _audit_change("remove", perm, user_or_group, None, count=len(objs))
    return len(objs)


def filter_by_perm(user, perm: str, queryset: models.QuerySet) -> models.QuerySet:
    """Rows of ``queryset`` the user may see under ``perm``: company scope first, then global permission (all rows in scope) or
    guardian object permissions (only granted rows). Anonymous/inactive -> none."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return queryset.none()
    model = queryset.model
    if hasattr(queryset, "for_company_ids") and getattr(model, "company_lookup", None):
        queryset = queryset.for_company_ids(user_company_ids(user))     # belt and braces; callers scope to the ACTIVE company
    if user.is_superuser:
        return queryset
    if perm == STAFF_PERM:
        return queryset if user.is_staff else queryset.none()
    if perm in STAFF_IMPLIED and user.is_staff:
        return queryset
    if user.has_perm(perm):                      # global grant => every row in the user's companies
        return queryset
    from guardian.shortcuts import get_objects_for_user

    granted = get_objects_for_user(user, perm, klass=model._base_manager.all(), accept_global_perms=False)
    return queryset.filter(pk__in=granted.values("pk"))


def invalidate() -> None:
    cache.bump()


def _audit_change(action: str, perm: str, target, obj, count: int = 1) -> None:
    """``permission`` row: who (the acting user), what (``perm``, assign / remove), to whom and on which object. A global grant
    (no object) is a ``user_permissions`` / ``permissions`` change, already logged on the user / role by ``core.audit.m2m``."""
    from core.audit.service import record_event

    if obj is None and count == 1:
        return
    label = getattr(target, "email", None) or getattr(target, "name", str(target))
    on = f" on {obj._meta.verbose_name} “{obj}” (#{obj.pk})" if obj is not None else ""
    record_event("permission", message=f"{action} {perm} -> {label}{on}" + (f" ({count} objects)" if count != 1 or obj is None else ""),
                 obj=obj, changes={"permission": [None, perm], "action": [None, action], "granted to": [None, label]},
                 **({} if obj is not None else {"model_label": target._meta.label, "object_pk": str(target.pk), "object_repr": str(target)}))
