"""Dynamic django-import-export Resources.

* :func:`model_export_resource` -- ModelResource for a registered CRUD resource (columns = allowlisted export fields).
* :func:`rows_resource` -- Resource for report rows (``values()`` dicts wrapped as attribute objects; columns -> Fields).
* :func:`model_import_resource` -- company scoped ModelResource for imports (dry-run preview, then commit).
Resources never contain user-supplied attribute paths: every column comes from the registry allowlist.
"""
from __future__ import annotations

from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import models
from import_export import fields as ie_fields
from import_export import resources


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralise(value):
    """Spreadsheet formula injection: text that a spreadsheet would execute gets a leading apostrophe (numbers are left alone)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        try:
            float(value)
            return value
        except ValueError:
            return "'" + value
    return value


class SafeExportMixin:
    """Every exported cell passes through :func:`neutralise` (signature-agnostic across django-import-export majors)."""

    def export_field(self, field, *args, **kwargs):
        return neutralise(super().export_field(field, *args, **kwargs))


def export_field_names(cfg) -> list[str]:
    if cfg.export_fields:
        return list(cfg.export_fields)
    skip = {"deleted_at", "deleted_by"}
    names = []
    for f in cfg.model._meta.concrete_fields:
        if f.name in skip or isinstance(f, (models.BinaryField, models.FileField)):
            continue
        names.append(f.name)
    return names


def model_export_resource(cfg, ctx=None, companies: dict | None = None):
    """``companies`` = ``{pk: "A, B"}`` adds a first "Company" column (report company scope with more than one company)."""
    names = export_field_names(cfg)
    attrs = {}
    if companies is not None:
        attrs["core_company"] = ie_fields.Field(column_name="Company")
        attrs["dehydrate_core_company"] = lambda self, obj: companies.get(obj.pk, "")
        names = ["core_company"] + names
    attrs["Meta"] = type("Meta", (), {"model": cfg.model, "fields": tuple(names), "export_order": tuple(names), "skip_unchanged": False})
    return type(f"{cfg.model.__name__}ExportResource", (SafeExportMixin, resources.ModelResource), attrs)()


def rows_resource(columns: list[tuple[str, str]]):
    """``columns`` = [(alias, label)]. Rows passed to ``export(queryset=...)`` must be objects with those attributes."""
    attrs = {alias: ie_fields.Field(attribute=alias, column_name=label) for alias, label in columns}
    meta = type("Meta", (), {"fields": tuple(a for a, _ in columns), "export_order": tuple(a for a, _ in columns)})
    attrs["Meta"] = meta
    return type("ReportRowsResource", (SafeExportMixin, resources.Resource), attrs)()


def as_row(mapping: dict) -> SimpleNamespace:
    return SimpleNamespace(**mapping)


# Never read from an import file: the company is forced, the created/updated stamps are the system's to set.
IMPORT_SKIP = ("company", "created_at", "created_by", "updated_at", "updated_by")


def model_import_resource(cfg, ctx):
    """Company-scoped import: only the user's active company; foreign ids are rejected; company is forced, never read from the file."""
    names = [n for n in export_field_names(cfg) if n not in IMPORT_SKIP]
    meta = type("Meta", (), {"model": cfg.model, "fields": tuple(names), "import_id_fields": ("id",), "skip_unchanged": False,
                             "use_transactions": True})

    def get_queryset(self):
        from core.crud.access import scoped_queryset

        return scoped_queryset(cfg, ctx, action="change")

    def before_import(self, dataset, **kwargs):
        if "id" not in (dataset.headers or []):        # a file of new rows has no id column: every row is created
            dataset.append_col([""] * len(dataset), header="id")

    def before_import_row(self, row, **kwargs):
        ident = row.get("id")
        if ident not in (None, ""):
            existing = cfg.model._base_manager.filter(pk=ident).first()
            if existing is not None and not get_queryset(self).filter(pk=ident).exists():
                raise ValidationError("This id belongs to a record you cannot change.")

    def before_save_instance(self, instance, row, **kwargs):
        if cfg.company_lookup == "company":
            if instance.pk is None or getattr(instance, "company_id", None) is None:
                instance.company_id = ctx.company_id
            elif instance.company_id != ctx.company_id:
                raise ValidationError("Cross-company import is not allowed.")
        if ctx.company_id is None and cfg.company_lookup:
            raise ValidationError("No active company.")

    attrs = {"Meta": meta, "get_queryset": get_queryset, "before_import": before_import, "before_import_row": before_import_row,
             "before_save_instance": before_save_instance}
    return type(f"{cfg.model.__name__}ImportResource", (resources.ModelResource,), attrs)()
