"""Audit of many-to-many changes (``m2m_changed``) on audited models: user companies, roles and permissions, role permissions and
members, approval step approvers / step adders, report sharing, ... -- any M2M whose *edited* object is an ``AuditTrackedModel``.

* The row goes on the object whose manager was called (``user.groups.add(...)`` -> the user; ``role.permissions.set(...)`` -> the
  role). ``Profile`` changes go on its **user** (company memberships show on the user's audit tab); a plain ``auth.Group`` is
  logged as its ``Role`` proxy.
* ``changes`` = ``{field: [old names, new names]}`` (sorted, permissions as ``app.codename``); the message lists what was added and
  removed. Roles / permissions / members are a ``permission`` event, everything else an ``update``.
* One row per operation: every change to the same field of the same object inside one transaction is collected and written when it
  commits (``set()`` = remove + add, one row); nothing changed (or rolled back) = no row. Context (user, IP, company) is taken at the
  first change. Never raises into the caller."""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from core.services import context

from .service import _active_company_id, is_tracked, record_event

log = logging.getLogger("core.audit")
PERMISSION_FIELDS = {"groups", "user_permissions", "permissions", "user_set"}
_PENDING_ATTR = "_core_audit_m2m"


def _through_field(sender, instance, reverse, model):
    """``(field key, source column, target column, related model)`` of the relation behind ``sender`` seen from ``instance``."""
    declaring = model if reverse else type(instance)
    for field in declaring._meta.many_to_many:
        if field.remote_field.through is not sender:
            continue
        if not reverse:
            return field.name, field.m2m_field_name(), field.m2m_reverse_field_name(), field.related_model
        accessor = field.remote_field.get_accessor_name()
        if not accessor or accessor.endswith("+"):
            return None
        return accessor, field.m2m_reverse_field_name(), field.m2m_field_name(), field.model
    return None


def _owner(instance):
    """``(model label, pk, repr)`` the row is written on, or None when the edited object is not audited."""
    from django.contrib.auth.models import Group

    from core.models import Profile, Role

    if isinstance(instance, Profile):
        user = instance.user
        return user._meta.label, user.pk, str(user)
    if type(instance) is Group:
        return Role._meta.label, instance.pk, str(instance)
    if is_tracked(type(instance)):
        return instance._meta.label, instance.pk, str(instance)
    return None


def _current(sender, source: str, target: str, pk) -> set:
    return set(sender._base_manager.filter(**{source: pk}).values_list(target, flat=True))


def _names(model, ids) -> dict:
    """``{id: text}``: permissions as ``app.codename``, everything else ``str(obj)`` (deleted rows included)."""
    from django.contrib.auth.models import Permission

    if not ids:
        return {}
    manager = getattr(model, "global_objects", None) or model._base_manager
    qs = manager.filter(pk__in=list(ids))
    if model is Permission:
        return {p.pk: f"{p.content_type.app_label}.{p.codename}" for p in qs.select_related("content_type")}
    return {o.pk: str(o) for o in qs}


@receiver(m2m_changed, dispatch_uid="core_audit_m2m")
def remember_m2m(sender, instance, action, reverse, model, pk_set=None, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear") or context.is_audit_suppressed() or instance.pk is None:
        return
    try:
        with transaction.atomic():       # savepoint: a failed query here must not abort the caller's transaction on PostgreSQL
            _remember(sender, instance, reverse, model)
    except Exception:  # noqa: BLE001 - auditing must not break the operation
        log.exception("m2m audit failed for %s", getattr(sender, "_meta", sender))


def _remember(sender, instance, reverse, model):
    owner = _owner(instance)
    found = _through_field(sender, instance, reverse, model) if owner else None
    if not found:
        return
    key, source, target, related = found
    connection = transaction.get_connection()
    pending = connection.__dict__.setdefault(_PENDING_ATTR, {})
    slot = (sender._meta.label, key, owner[0], owner[1])
    entry = pending.get(slot)
    if entry is not None and _queued(connection, entry["flush"]):
        return                                           # already collecting this field in this transaction: keep the first "old"
    entry = {"old": _current(sender, source, target, instance.pk), "sender": sender, "source": source, "target": target,
             "related": related, "key": key, "owner": owner, "pk": instance.pk, "user": context.current_user(),
             "ip": context.current_ip(), "company": _active_company_id()}
    entry["flush"] = lambda: _flush(slot)
    pending[slot] = entry
    transaction.on_commit(entry["flush"], robust=True)


def _queued(connection, func) -> bool:
    """True while ``func`` is still waiting for the commit (a rolled-back block drops its callbacks)."""
    return any(item[1] is func for item in getattr(connection, "run_on_commit", ()))


def _flush(slot) -> None:
    entry = transaction.get_connection().__dict__.get(_PENDING_ATTR, {}).pop(slot, None)
    if entry is None:
        return
    try:
        new = _current(entry["sender"], entry["source"], entry["target"], entry["pk"])
        old = entry["old"]
        if new == old:
            return
        names = _names(entry["related"], old | new)
        label = lambda ids: sorted(names.get(i, f"#{i} (deleted)") for i in ids)      # noqa: E731
        added, removed = label(new - old), label(old - new)
        key = entry["key"]
        message = "; ".join(part for part in (f"{key}: added {', '.join(added)}" if added else "",
                                              f"{key}: removed {', '.join(removed)}" if removed else "") if part)
        model_label, pk, text = entry["owner"]
        record_event("permission" if key in PERMISSION_FIELDS else "update", model_label=model_label, object_pk=str(pk), object_repr=text,
                     changes={key: [label(old), label(new)]}, message=message, user=entry["user"], ip=entry["ip"],
                     company=entry["company"])
    except Exception:  # noqa: BLE001
        log.exception("m2m audit flush failed")
