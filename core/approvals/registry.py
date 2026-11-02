"""Approval registries (same pattern as the search/report registries): code-driven chains, targets and skip rules, plus
targets configured in the app.

``CHAINS`` holds chain definitions; ``sync_chain``/``sync_all_chains`` create the matching ``ApprovalType`` / ``ApprovalStep`` /
``ApprovalTypeStep`` rows when they are missing (idempotent). Once a type exists its steps belong to the administrators: syncing
never edits, reorders or deletes them. Exactly one step per chain is ``is_final`` (the last one of the code definition).

Targets: a model is approvable when code calls ``register_target`` (it must inherit ``AuditedModel``), or when an active
``ApprovalType`` names it in *Applies to* -- any registered resource model, no code needed. A code target wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction

from .errors import ApprovalError


@dataclass
class StepSpec:
    code: str
    name: str
    permission: str = ""
    skip_rule: str = ""
    enabled: bool = True


@dataclass
class ChainSpec:
    code: str
    name: str
    steps: list[StepSpec]
    reject_mode: str = "return_to_submitter"
    permission: str = ""

    def __post_init__(self):
        if len(self.steps) < 1:
            raise ImproperlyConfigured(f"Chain {self.code!r} needs at least one step (the last one is final).")
        codes = [s.code for s in self.steps]
        if len(set(codes)) != len(codes):
            raise ImproperlyConfigured(f"Chain {self.code!r} has duplicate step codes.")
        if self.reject_mode not in ("return_to_submitter", "previous_step"):
            raise ImproperlyConfigured("reject_mode must be 'return_to_submitter' or 'previous_step'")
        final = self.steps[-1]
        if final.skip_rule or not final.enabled:
            raise ImproperlyConfigured(f"Chain {self.code!r}: the final step cannot be disabled or auto-skipped.")


CHAINS: dict[str, ChainSpec] = {}


def register_chain(code: str, name: str, steps: Sequence[StepSpec | dict], *, reject_mode: str = "return_to_submitter",
                   permission: str = "") -> ChainSpec:
    specs = [s if isinstance(s, StepSpec) else StepSpec(**s) for s in steps]
    chain = ChainSpec(code, name, specs, reject_mode, permission)
    CHAINS[code] = chain
    return chain


@dataclass
class TargetConfig:
    key: str
    model: type[models.Model]
    label: str
    chain: str
    url_builder: Callable | None = None
    title_builder: Callable | None = None
    submit_permission: str | None = None
    on_approved: Callable | None = None
    on_rejected: Callable | None = None
    lock_statuses: tuple[str, ...] = ("pending", "approved")

    def url(self, obj) -> str:
        if self.url_builder:
            return self.url_builder(obj)
        from django.urls import NoReverseMatch, reverse

        from core.crud.registry import resource_for_model

        cfg = resource_for_model(type(obj))
        try:
            return reverse("core:resource_detail", args=[cfg.slug, obj.pk]) if cfg else ""
        except NoReverseMatch:
            return ""

    def title(self, obj) -> str:
        return self.title_builder(obj) if self.title_builder else str(obj)


_TARGETS: dict[str, TargetConfig] = {}
_SKIP_RULES: dict[str, Callable] = {}


def register_target(target_key: str, model, label: str = "", url_builder=None, title_builder=None, submit_permission: str | None = None,
                    on_approved=None, on_rejected=None, lock_statuses: Sequence[str] = ("pending", "approved"), chain: str | None = None
                    ) -> TargetConfig:
    from core.models.approvals import AuditedModel

    if not (isinstance(model, type) and issubclass(model, AuditedModel)):
        raise ImproperlyConfigured(f"{model!r} must inherit core.models.AuditedModel to be an approval target.")
    bad = set(lock_statuses) - {"pending", "approved"}
    if bad:
        raise ImproperlyConfigured(f"lock_statuses may contain only 'pending' and 'approved', got {sorted(bad)}")
    cfg = TargetConfig(target_key, model, label or str(model._meta.verbose_name).title(), chain or target_key, url_builder, title_builder,
                       submit_permission, on_approved, on_rejected, tuple(lock_statuses))
    _TARGETS[target_key] = cfg
    return cfg


def get_target(key: str) -> TargetConfig:
    try:
        return _TARGETS[key]
    except KeyError:
        raise ApprovalError("This record type is not set up for approvals.") from None


DYNAMIC_PREFIX = "type:"


def bound_type(obj_or_model, company_id=None):
    """The active ApprovalType configured in the app for this model: the record's company's own type first, else a type for every
    company. None when the model has no such type."""
    from django.contrib.contenttypes.models import ContentType

    from core.models import ApprovalType

    model = obj_or_model if isinstance(obj_or_model, type) else type(obj_or_model)
    ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
    qs = ApprovalType.objects.filter(content_type=ct, is_active=True).order_by("pk")
    own = qs.filter(company_id=company_id).first() if company_id else None
    return own or qs.filter(company__isnull=True).first()


def _record_company(obj):
    from core.crud.registry import resource_for_model

    lookup = getattr(obj, "company_lookup", None)
    if lookup is None:
        cfg = resource_for_model(type(obj))
        lookup = cfg.company_lookup if cfg else None
    return getattr(obj, "company_id", None) if lookup == "company" else None


def dynamic_target(obj) -> TargetConfig | None:
    atype = bound_type(obj, _record_company(obj))
    if atype is None:
        return None
    model = type(obj)
    return TargetConfig(f"{DYNAMIC_PREFIX}{atype.code}", model, atype.name, atype.code)


def target_for(obj) -> TargetConfig:
    key = getattr(obj, "approval_target", "") or ""
    if key and key in _TARGETS:
        return _TARGETS[key]
    target = dynamic_target(obj)
    if target is None:
        raise ApprovalError("This record type is not set up for approvals.")
    return target


def model_lock_statuses(model) -> tuple[str, ...] | None:
    """Statuses that lock records of ``model`` for editing, or None when the model has no approval at all (list pages ask once
    per page instead of once per row)."""
    from django.contrib.contenttypes.models import ContentType

    from core.models import ApprovalType

    code = next((t for t in _TARGETS.values() if t.model is model), None)
    if code is not None:
        return code.lock_statuses
    ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
    return ("pending", "approved") if ApprovalType.objects.filter(content_type=ct, is_active=True).exists() else None


def has_approval(obj) -> bool:
    try:
        target_for(obj)
    except ApprovalError:
        return False
    return True


def all_targets() -> list[TargetConfig]:
    return list(_TARGETS.values())


def register_skip_rule(name: str, func: Callable) -> None:
    """``func(instance, request) -> bool``; True skips the step (logged in the history)."""
    _SKIP_RULES[name] = func


def get_skip_rule(name: str) -> Callable:
    try:
        return _SKIP_RULES[name]
    except KeyError:
        raise ApprovalError(f"Approval rule {name!r} is not registered.") from None


def sync_chain(code: str):
    """Create the DB rows of one code-defined chain if they are missing. Returns the ApprovalType. A type that already has steps
    is returned untouched: administrators may have reordered, re-assigned or disabled them in the app."""
    from core.models import ApprovalStep, ApprovalType, ApprovalTypeStep

    try:
        spec = CHAINS[code]
    except KeyError:
        raise ApprovalError(f"Approval chain {code!r} is not defined.") from None
    with transaction.atomic():
        atype, _ = ApprovalType.objects.get_or_create(code=code, defaults={"name": spec.name, "reject_mode": spec.reject_mode,
                                                                           "permission": spec.permission})
        if atype.type_steps.exists():
            return atype
        for position, s in enumerate(spec.steps, start=1):
            step, _ = ApprovalStep.objects.get_or_create(code=s.code, defaults={"name": s.name, "permission": s.permission,
                                                                                "skip_rule": s.skip_rule})
            ApprovalTypeStep.objects.create(approval_type=atype, step=step, position=position * 10, is_final=position == len(spec.steps),
                                            is_enabled=s.enabled, permission=s.permission)
    return atype


def sync_all_chains() -> list[str]:
    return [sync_chain(code).code for code in CHAINS]
