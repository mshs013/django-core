from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.services import context

from .redaction import clean_value, redact_mapping


def snapshot(instance: models.Model) -> dict:
    """Concrete, non-secret field values of an instance (raw column values, so related objects are ids). File / image fields keep
    only the stored name, so a new upload shows up as a change; the audit view never shows the name or the file."""
    exclude = set(getattr(instance, "audit_exclude_fields", ()))
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude or isinstance(field, models.BinaryField):
            continue
        if isinstance(field, models.FileField):
            data[field.name] = getattr(field.value_from_object(instance), "name", None) or None
            continue
        data[field.name] = clean_value(_as_stored(field, field.value_from_object(instance)))
    return redact_mapping(data, exclude)


def _as_stored(field, value):
    """The value as the database will hand it back, so an unsaved ``price = 7`` and a loaded ``Decimal("7.00")`` compare equal and
    no change is logged that never happened."""
    if value is None or field.is_relation:
        return value
    try:
        value = field.to_python(value)
        if isinstance(field, models.DecimalField) and value is not None and field.decimal_places is not None:
            value = value.quantize(Decimal(1).scaleb(-field.decimal_places))
    except (ValidationError, ArithmeticError, TypeError, ValueError):
        pass                                     # unexpected value: snapshot it as given rather than fail the save
    return value


def is_tracked(model) -> bool:
    from core.models.base import AuditTrackedModel

    return isinstance(model, type) and issubclass(model, AuditTrackedModel) and not model._meta.abstract


def _active_company_id():
    """The acting company: the context's active company, else the single company of the request's scope (the middleware sets it)."""
    if context.current_company() is not None:
        return context.current_company().pk
    ids = context.current_company_ids() or ()
    return next(iter(ids)) if len(ids) == 1 else None


def _company_id_of(obj):
    if obj is not None and getattr(obj, "company_lookup", None) == "company":
        return getattr(obj, "company_id", None)
    return _active_company_id()


def record_event(event: str, *, obj: models.Model | None = None, changes: dict | None = None, message: str = "", user=None,
                 company=None, model_label: str = "", object_pk: str = "", object_repr: str = "", snapshot: dict | None = None,
                 ip: str | None = None, user_repr: str = "") -> None:
    """Append one audit row. ``changes`` is redacted again here as a safety net. Never raises into the caller's business logic."""
    import logging

    from core.models import AuditLog

    if context.is_audit_suppressed():
        return
    try:
        user = user if user is not None else context.current_user()
        company_id = getattr(company, "pk", company) or _company_id_of(obj)
        with transaction.atomic():      # savepoint: a failed write must not abort the caller's transaction on PostgreSQL
            AuditLog.objects.create(
                event=event, user=user if getattr(user, "pk", None) else None, user_repr=(getattr(user, "email", "") or user_repr or "")[:200],
                company_id=company_id,
                model_label=(obj._meta.label if obj is not None else model_label)[:100],
                object_pk=str(obj.pk) if obj is not None and obj.pk is not None else object_pk[:64],
                object_repr=(str(obj) if obj is not None else object_repr)[:300],
                changes=redact_mapping(changes or {}), snapshot=redact_mapping(snapshot) if snapshot else None,
                message=message[:500], ip_address=(ip if ip is not None else context.current_ip()) or None)
    except Exception:  # noqa: BLE001 - auditing must not break the operation; failure is logged without payload
        logging.getLogger("core.audit").exception("audit write failed for event=%s", event)


def audited_update(queryset, *, message: str = "bulk update", **values) -> int:
    """``queryset.update(**values)`` that still writes one ``update`` row (with snapshot) per changed row of an audited model --
    ``QuerySet.update()`` sends no signals. Plain ``update()`` for models that are not audited."""
    model = queryset.model
    if not is_tracked(model) or context.is_audit_suppressed():
        return queryset.update(**values)
    from core.models import AuditLog

    from .redaction import diff_snapshots

    manager = getattr(model, "global_objects", None) or model._base_manager
    before = {obj.pk: snapshot(obj) for obj in queryset}
    count = manager.filter(pk__in=list(before)).update(**values)
    for obj in manager.filter(pk__in=list(before)):
        new = snapshot(obj)
        changes = diff_snapshots(before.get(obj.pk), new)
        if changes:
            record_event(AuditLog.UPDATE, obj=obj, changes=changes, snapshot=new, message=message)
    return count
