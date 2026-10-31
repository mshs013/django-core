"""Acting context + fail-closed object access for CRUD views and services."""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.http import Http404

from core.permissions import active_company_id, filter_by_perm, user_company_ids, user_has_perm
from core.permissions.access import restrict
from core.permissions.scope import scope_queryset


@dataclass(frozen=True)
class Ctx:
    user: object
    company_ids: frozenset          # data scope: the ACTIVE company (reports: core.reports.scope); empty -> fail closed
    company_id: int | None
    member_ids: frozenset = frozenset()
    report_scope: str = ""          # reports only: active | all | chosen (core.reports.scope); "" everywhere else

    @classmethod
    def from_request(cls, request) -> "Ctx":
        user = request.user
        active = active_company_id(user)
        return cls(user=user, company_ids=frozenset({active}) if active else frozenset(), company_id=active,
                   member_ids=user_company_ids(user))


def scoped_queryset(cfg, ctx: Ctx, *, mode: str = "alive", action: str = "view"):
    """Company scope + row-level access + object permission. ``mode``: alive | deleted | all. Fail closed: no companies -> empty."""
    model = cfg.model
    if mode == "deleted" and cfg.is_soft:
        qs = model.deleted_objects.all()
    elif mode == "all" and cfg.is_soft:
        qs = model.global_objects.all()
    else:
        qs = model._default_manager.all()
    hook = cfg.hook("get_queryset")
    if hook:
        qs = hook(qs, ctx)
    if cfg.company_lookup and not (cfg.superuser_unscoped and getattr(ctx.user, "is_superuser", False)):
        qs = scope_queryset(qs, cfg.company_lookup, ctx.company_ids)
    qs = restrict(qs, ctx.user)                                   # object access / user access (row level, see core.permissions.access)
    perm = cfg.perm(action)
    if perm is None:                                              # "any signed-in user" resources (own notifications, own jobs)
        return qs if getattr(ctx.user, "is_authenticated", False) else qs.none()
    return filter_by_perm(ctx.user, perm, qs)


def allowed(user, perm, obj=None) -> bool:
    """``perm`` None means any authenticated user (data is still scoped by the resource's queryset hook)."""
    if perm is None:
        return bool(getattr(user, "is_authenticated", False) and user.is_active)
    return user_has_perm(user, perm, obj)


def get_object_or_404(cfg, ctx: Ctx, pk, *, mode: str = "alive", action: str = "view"):
    """Out-of-scope or unknown ids are indistinguishable (404); an in-scope object without the permission is 403."""
    base = scoped_queryset(cfg, ctx, mode=mode, action="view")
    try:
        obj = base.get(pk=pk)
    except (cfg.model.DoesNotExist, ValueError, TypeError):
        raise Http404("Not found") from None
    except Exception as exc:  # ValidationError for malformed UUIDs
        if exc.__class__.__name__ == "ValidationError":
            raise Http404("Not found") from None
        raise
    if action != "view" and (not cfg.allows(action) or not allowed(ctx.user, cfg.perm(action), obj)):
        raise PermissionDenied
    return obj
