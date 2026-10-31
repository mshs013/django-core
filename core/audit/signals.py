"""Audit hooks for models that inherit ``AuditTrackedModel`` (opt-in). High-volume models simply do not inherit it."""
from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models.audit import AuditLog
from core.signals import post_restore, post_soft_delete

from . import m2m  # noqa: F401  (many-to-many changes: core.audit.m2m)
from .redaction import diff_snapshots
from .service import is_tracked as _tracked, record_event, snapshot


@receiver(pre_save, dispatch_uid="core_audit_pre_save")
def remember_old(sender, instance, raw=False, **kwargs):
    if raw or not _tracked(sender):
        return
    instance._audit_old = None
    if instance.pk and not instance._state.adding:
        old = sender._base_manager.filter(pk=instance.pk).first()
        instance._audit_old = snapshot(old) if old is not None else None


@receiver(post_save, dispatch_uid="core_audit_post_save")
def write_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _tracked(sender):
        return
    new = snapshot(instance)
    if created:
        record_event(AuditLog.CREATE, obj=instance, changes=diff_snapshots({}, new), snapshot=new)
        return
    changes = diff_snapshots(getattr(instance, "_audit_old", None), new)
    changes.pop("deleted_at", None)
    changes.pop("deleted_by", None)
    if changes:
        record_event(AuditLog.UPDATE, obj=instance, changes=changes, snapshot=new)


@receiver(post_soft_delete, dispatch_uid="core_audit_soft_delete")
def write_delete(sender, instance, user=None, **kwargs):
    if _tracked(sender):
        record_event(AuditLog.DELETE, obj=instance, user=user, message="soft delete", snapshot=snapshot(instance))


@receiver(post_restore, dispatch_uid="core_audit_restore")
def write_restore(sender, instance, user=None, **kwargs):
    if _tracked(sender):
        record_event(AuditLog.RESTORE, obj=instance, user=user)


from django.db.models.signals import post_delete  # noqa: E402


@receiver(post_delete, dispatch_uid="core_audit_hard_delete")
def write_hard_delete(sender, instance, **kwargs):
    if _tracked(sender):
        record_event(AuditLog.DELETE, obj=instance, message="hard delete", snapshot=snapshot(instance))
