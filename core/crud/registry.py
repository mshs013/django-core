"""CRUD resource registry. Business apps call :func:`register_resource` from ``AppConfig.ready()``; ``core`` never imports them.

Everything reachable from a URL is resolved through this allowlist: ``/r/<slug>/`` -> ``ResourceConfig`` -> model. Raw model paths,
field names and lookups from the request are never used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.text import slugify

from core.search.registry import register_search, resolve_path, unregister_search

ACTIONS = ("view", "add", "change", "delete")
DETAIL_EXPORTS = ("pdf", "xlsx")
DETAIL_EXPORT_WHEN = ("always", "approved")


@dataclass
class Inline:
    """A child collection edited inside the parent's form.

    kind ``fk``      reverse FK / O2O (``fk_name`` needed when the child has several FKs to the parent)
    kind ``through`` M2M through model: an FK inline over the through model, so extra columns (qty, price) are editable
    kind ``generic`` GenericRelation child (``ct_field``/``fk_field`` default to Django's names)
    ``nested`` = grandchildren (any depth); ``position_field`` keeps the DOM order of rows (0..n)."""

    model: type[models.Model]
    fields: Sequence[str] = ()
    fk_name: str | None = None
    slug: str = ""
    label: str = ""
    kind: str = "fk"
    extra: int = 1
    min_num: int = 0
    can_delete: bool = True
    hard_delete: bool = False
    position_field: str | None = None
    ct_field: str = "content_type"
    fk_field: str = "object_id"
    nested: Sequence["Inline"] = ()
    widgets: dict = field(default_factory=dict)
    m2m_widgets: dict = field(default_factory=dict)           # many-to-many field -> "select" | "checkbox"
    permission_prefix: str = ""          # default: the child model's own add/change/delete codenames

    def __post_init__(self):
        self.slug = self.slug or slugify(self.model._meta.model_name)
        self.label = self.label or str(self.model._meta.verbose_name_plural).title()
        if self.kind not in ("fk", "through", "generic"):
            raise ImproperlyConfigured(f"Inline kind {self.kind!r} is not one of fk/through/generic")
        if not self.fields:
            raise ImproperlyConfigured(f"Inline {self.model.__name__}: list the editable fields explicitly")
        for name in self.fields:
            self.model._meta.get_field(name)
        if self.position_field and self.position_field not in self.fields:
            raise ImproperlyConfigured("position_field must also be listed in fields (it is rendered as a hidden input)")

    @property
    def is_soft(self) -> bool:
        from core.models.base import SoftDeleteModel

        return issubclass(self.model, SoftDeleteModel)

    @property
    def deletable(self) -> bool:
        return self.can_delete and (self.is_soft or self.hard_delete)


@dataclass
class ResourceConfig:
    model: type[models.Model]
    slug: str
    label: str
    label_plural: str
    icon: str = "box"
    list_fields: tuple[str, ...] = ()
    detail_fields: tuple[str, ...] = ()
    form_fields: tuple[str, ...] = ()
    readonly_fields: tuple[str, ...] = ()
    widgets: dict = field(default_factory=dict)
    layout: Sequence[Sequence[str]] = ()                    # rows of field names for the form (optional)
    searchable_fields: tuple[str, ...] = ()
    filterable_fields: Any = ()
    searchable_relations: tuple[str, ...] = ()
    sortable_fields: tuple[str, ...] = ()
    default_sort: tuple[str, ...] = ()
    inlines: tuple[Inline, ...] = ()
    generic_fks: tuple[str, ...] = ()
    permissions: dict = field(default_factory=dict)         # action -> "app_label.codename" overrides
    company_lookup: str | None = None                       # "company" | "companies" | None
    actions: dict = field(default_factory=dict)             # name -> callable(queryset, request) for bulk actions
    templates: dict = field(default_factory=dict)           # list/detail/form/confirm -> template name overrides
    hooks: dict = field(default_factory=dict)               # before_save/after_save/delete_guard/before_delete/after_delete/get_queryset/detail_extra
    menu: bool = True
    allow_export: bool = True
    allow_import: bool = False
    export_fields: tuple[str, ...] = ()
    page_size: int = 25
    disabled_actions: tuple[str, ...] = ()                  # subset of add/change/delete (read-only resources)
    superuser_unscoped: bool = False                        # ONLY for core's own cross-company views (audit, jobs)
    form_class: Callable | None = None                      # factory ``(cfg, ctx) -> ModelForm class`` replacing the generated form (e.g. user + profile)
    date_formats: dict = field(default_factory=dict)        # {"date"|"datetime"|"time": format} overriding the site formats here
    tabs: tuple = ()                                        # (("Tab label", ("field", "@inline_slug", ...)), ...) for the form
    m2m_widgets: dict = field(default_factory=dict)         # many-to-many field -> "select" | "checkbox" (default CORE_M2M_WIDGET)
    owner_field: str | None = None                          # FK to the user: users see own + assigned users' rows (core.permissions.access)
    detail_exports: tuple[str, ...] = DETAIL_EXPORTS        # PDF / Excel buttons on the detail page ("pdf", "xlsx"); () = none
    detail_export_when: str = "always"                      # "always" | "approved" (approvable records only); the approval type may override both
    labels: dict = field(default_factory=dict)              # column / field name -> heading, e.g. {"profile__dept__name": "Department"}

    def allows(self, action: str) -> bool:
        return action not in self.disabled_actions

    @property
    def key(self) -> str:
        return self.slug

    @property
    def is_soft(self) -> bool:
        from core.models.base import SoftDeleteModel

        return issubclass(self.model, SoftDeleteModel)

    def perm(self, action: str) -> str | None:
        if action in self.permissions:
            return self.permissions[action]
        meta = self.model._meta
        return f"{meta.app_label}.{action}_{meta.model_name}"

    def hook(self, name: str) -> Callable | None:
        return self.hooks.get(name)

    def field_label(self, name: str) -> str:
        """Heading of a list column / detail field: ``labels`` first, else the model's verbose name."""
        from core.crud.service import field_label

        return str(self.labels.get(name) or field_label(self.model, name))

    def template(self, name: str, default: str) -> str:
        return self.templates.get(name, default)


_RESOURCES: dict[str, ResourceConfig] = {}
_MODEL_INDEX: dict[type, str] = {}


def _check_fields(model, names: Sequence[str], what: str, allow_attrs: bool = False) -> None:
    for name in names:
        if name == "__str__":
            continue
        try:
            model._meta.get_field(name)
        except Exception:
            if allow_attrs and hasattr(model, name):
                continue
            if allow_attrs and "__" in name:
                resolve_path(model, name)                   # a related value, e.g. 'profile__dept__name' (sortable too)
                continue
            raise ImproperlyConfigured(f"{model._meta.label}: {what} {name!r} is not a field of the model") from None


def register_resource(model: type[models.Model], *, slug: str = "", label: str = "", label_plural: str = "", icon: str = "box",
                      list_fields: Sequence[str] = (), detail_fields: Sequence[str] = (), form_fields: Sequence[str] = (),
                      readonly_fields: Sequence[str] = (), widgets: dict | None = None, layout: Sequence[Sequence[str]] = (),
                      searchable_fields: Sequence[str] = (), filterable_fields: Any = (), searchable_relations: Sequence[str] = (),
                      sortable_fields: Sequence[str] = (), default_sort: Sequence[str] = (), inlines: Sequence[Inline] = (),
                      generic_fks: Sequence[str] = (), permissions: dict | None = None, company_field: str | None = "auto",
                      actions: dict | None = None, templates: dict | None = None, hooks: dict | None = None, menu: bool = True,
                      allow_export: bool = True, allow_import: bool = False, export_fields: Sequence[str] = (),
                      page_size: int = 25, disabled_actions: Sequence[str] = (), superuser_unscoped: bool = False,
                      form_class: Callable | None = None, date_formats: dict | None = None, tabs: Sequence = (),
                      m2m_widgets: dict | None = None, owner_field: str | None = None,
                      detail_exports: Sequence[str] = DETAIL_EXPORTS, detail_export_when: str = "always",
                      labels: dict | None = None) -> ResourceConfig:
    meta = model._meta
    if meta.abstract:
        raise ImproperlyConfigured(f"{model.__name__} is abstract; register its concrete (multi-table / proxy / plain) subclasses.")
    slug = slug or slugify(f"{meta.app_label}-{meta.model_name}")
    if slug in _RESOURCES:
        raise ImproperlyConfigured(f"Resource {slug!r} is already registered.")
    if company_field == "auto":
        company_field = getattr(model, "company_lookup", None)
    if company_field:
        resolve_path(model, company_field)                  # any ORM path that ends in a company id, e.g. 'profile__companies'
    bad_actions = set(disabled_actions) - {"add", "change", "delete"}
    if bad_actions:
        raise ImproperlyConfigured(f"disabled_actions may contain add/change/delete only, got {sorted(bad_actions)}")
    list_fields = tuple(list_fields) or tuple(f.name for f in meta.concrete_fields if f.editable and not f.primary_key)[:6]
    _check_fields(model, list_fields, "list field", allow_attrs=True)
    _check_fields(model, detail_fields, "detail field", allow_attrs=True)
    _check_fields(model, form_fields, "form field")
    _check_fields(model, readonly_fields, "read-only field", allow_attrs=True)
    for name in generic_fks:
        from django.contrib.contenttypes.fields import GenericForeignKey

        if not any(isinstance(p, GenericForeignKey) and p.name == name for p in meta.private_fields):
            raise ImproperlyConfigured(f"{model._meta.label}: {name!r} is not a GenericForeignKey")
    from core.crud.widgets import m2m_widget

    for name, kind in (m2m_widgets or {}).items():
        m2m_widget(kind)
        if not getattr(_field_or_none(model, name), "many_to_many", False):
            raise ImproperlyConfigured(f"{model._meta.label}: m2m_widgets {name!r} is not a many-to-many field")
    if owner_field:
        from django.contrib.auth import get_user_model

        owner = _field_or_none(model, owner_field)
        if owner is None or not owner.many_to_one or owner.related_model is not get_user_model():
            raise ImproperlyConfigured(f"{model._meta.label}: owner_field {owner_field!r} must be a foreign key to the user model")
    if set(detail_exports) - set(DETAIL_EXPORTS) or detail_export_when not in DETAIL_EXPORT_WHEN:
        raise ImproperlyConfigured(f"{model._meta.label}: detail_exports takes {DETAIL_EXPORTS}, detail_export_when one of {DETAIL_EXPORT_WHEN}")
    tabs = tuple((str(label), tuple(names)) for label, names in tabs)
    for _label, names in tabs:
        for name in names:
            if name.startswith("@"):
                if not any(i.slug == name[1:] for i in inlines):
                    raise ImproperlyConfigured(f"{model._meta.label}: tab entry {name!r} is not an inline slug")
    cfg = ResourceConfig(
        model=model, slug=slug, label=label or str(meta.verbose_name).title(), label_plural=label_plural or str(meta.verbose_name_plural).title(),
        icon=icon, list_fields=list_fields, detail_fields=tuple(detail_fields), form_fields=tuple(form_fields),
        readonly_fields=tuple(readonly_fields), widgets=widgets or {}, layout=tuple(tuple(r) for r in layout),
        searchable_fields=tuple(searchable_fields), filterable_fields=filterable_fields, searchable_relations=tuple(searchable_relations),
        sortable_fields=tuple(sortable_fields) or tuple(f for f in list_fields if f != "__str__" and _is_field(model, f)),
        default_sort=tuple(default_sort), inlines=tuple(inlines), generic_fks=tuple(generic_fks), permissions=permissions or {},
        company_lookup=company_field, actions=actions or {}, templates=templates or {}, hooks=hooks or {}, menu=menu,
        allow_export=allow_export, allow_import=allow_import, export_fields=tuple(export_fields), page_size=page_size,
        disabled_actions=tuple(disabled_actions), superuser_unscoped=superuser_unscoped, form_class=form_class,
        date_formats=dict(date_formats or {}), tabs=tabs, m2m_widgets=dict(m2m_widgets or {}),
        owner_field=owner_field or None, detail_exports=tuple(detail_exports), detail_export_when=detail_export_when,
        labels={str(k): str(v) for k, v in (labels or {}).items()})
    register_search(target_type="resource", key=slug, model=model, label=cfg.label_plural, searchable_fields=cfg.searchable_fields,
                    filterable_fields=cfg.filterable_fields, searchable_relations=cfg.searchable_relations,
                    sortable_fields=cfg.sortable_fields, default_sort=cfg.default_sort or ("-pk",))
    _RESOURCES[slug] = cfg
    _MODEL_INDEX[model] = slug
    return cfg


def _field_or_none(model, name: str):
    try:
        return model._meta.get_field(name)
    except Exception:
        return None


def _is_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


def get_resource(slug: str) -> ResourceConfig | None:
    return _RESOURCES.get(slug)


def resource_for_model(model) -> ResourceConfig | None:
    slug = _MODEL_INDEX.get(model)
    return _RESOURCES.get(slug) if slug else None


def all_resources() -> list[ResourceConfig]:
    return sorted(_RESOURCES.values(), key=lambda c: c.label_plural.lower())


def unregister_resource(slug: str) -> None:
    cfg = _RESOURCES.pop(slug, None)
    if cfg is not None:
        _MODEL_INDEX.pop(cfg.model, None)
        unregister_search("resource", slug)
