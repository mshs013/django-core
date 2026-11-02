"""What the seeder fills, and how apps can steer it.

By default every model registered with ``core.crud.registry.register_resource`` is seeded (core's own configuration/log models
excepted), together with the children of its inlines. Apps that need more control call :func:`register_seeder` from their
``AppConfig.ready`` -- but only when the seeder is installed, so the app keeps working once ``seeder`` is removed::

    from django.apps import apps
    if apps.is_installed("core.seeder"):
        from core.seeder import register_seeder
        register_seeder(Order, fields={"ref": lambda ctx, n: f"PO-{n:04d}"}, count=30)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.db import models

# core models that are configuration, logs or system state: never faked (users/companies/departments/designations have
# built-in seeders, approvals/notifications/mail are produced by the "activity" step instead)
CORE_SKIP = {"permission", "group", "role", "approvalstep", "approvaltype", "approvaltypestep", "approvalrequest", "approval",
             "notificationrule", "notification", "auditlog", "exportjob", "backuprecord", "archivejob", "savedsearch",
             "reportdefinition", "queryreport", "menuitem", "objectaccess", "useraccess", "profile"}


@dataclass
class SeederSpec:
    model: type[models.Model]
    count: int | None = None                    # rows per run (default: the run's count)
    fields: dict = field(default_factory=dict)  # field name -> value or callable(ctx, n) -> value
    factory: Callable | None = None             # callable(ctx, n) -> saved instance; replaces the generic generator
    skip: bool = False                          # never seed this model
    after: Callable | None = None               # callable(ctx, obj) after the row (and its inlines) is saved
    label: str = ""


_SPECS: dict[type, SeederSpec] = {}


def register_seeder(model, *, count: int | None = None, fields: dict | None = None, factory: Callable | None = None, skip: bool = False,
                    after: Callable | None = None, label: str = "") -> SeederSpec:
    spec = SeederSpec(model, count, dict(fields or {}), factory, skip, after, label)
    _SPECS[model] = spec
    return spec


def spec_for(model) -> SeederSpec | None:
    return _SPECS.get(model)


def unregister_seeder(model) -> None:
    _SPECS.pop(model, None)


def seedable_models() -> list[type[models.Model]]:
    """Every model the seeder can fill, in registry order (dependency order is worked out by the engine)."""
    from core.crud.registry import all_resources

    out: list = []
    for cfg in all_resources():
        model = cfg.model._meta.concrete_model if cfg.model._meta.proxy else cfg.model
        if model._meta.app_label in ("auth", "contenttypes", "sessions") or (model._meta.app_label == "core" and model._meta.model_name in CORE_SKIP):
            continue
        if model not in out:
            out.append(model)
    for model, spec in _SPECS.items():
        if model not in out:
            out.append(model)
    return [m for m in out if not (spec_for(m) and spec_for(m).skip)]
