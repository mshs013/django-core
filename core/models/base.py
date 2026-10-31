"""Reusable abstract models and managers.

* :class:`CreatedInfoModel` / :class:`UpdatedInfoModel` -- who/when stamps (filled from the request context).
* :class:`CompanyScopedModel` (FK) / :class:`MultiCompanyScopedModel` (M2M) -- fail-closed company scoping on the manager.
* :class:`SoftDeleteModel` -- ``objects`` (alive), ``deleted_objects`` (trashed), ``global_objects`` (both).
* :class:`AuditTrackedModel` -- opt-in marker: create/update/delete/restore write old/new snapshots to the audit log.

Combine company scoping and soft delete with :class:`CompanyScopedSoftDeleteModel` (or build your own manager from
``CompanyQuerySetMixin`` + ``SoftDeleteQuerySetMixin``): Django uses the *first* manager declared as the default manager, and
``core.E001`` fails the system check if a soft-delete model's default manager can expose deleted rows.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.utils import timezone

from core.services import context
from core.signals import post_restore, post_soft_delete


# --------------------------------------------------------------------------------------------------------------------- info
class CreatedInfoModel(models.Model):
    created_at = models.DateTimeField("created at", auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="created by")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self._state.adding and self.created_by_id is None:
            user = context.current_user()
            if user is not None:
                self.created_by = user
        super().save(*args, **kwargs)


class UpdatedInfoModel(models.Model):
    updated_at = models.DateTimeField("updated at", auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="updated by")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = context.current_user()
        if user is not None:
            self.updated_by = user
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "updated_by" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "updated_by"]
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------------------------------------------------- company scope
class CompanyQuerySetMixin:
    """Queryset helpers. ``company_lookup`` is ``"company"`` (FK) or ``"companies"`` (M2M)."""

    company_lookup = "company"

    def for_company_ids(self, company_ids):
        """Rows of the given companies. ``None``/empty -> nothing (fail closed)."""
        ids = [i for i in (company_ids or ()) if i is not None]
        if not ids:
            return self.none()
        qs = self.filter(**{f"{self.company_lookup}__in": ids})
        return qs.distinct() if self.company_lookup == "companies" else qs

    def for_company(self, company):
        return self.for_company_ids([getattr(company, "pk", company)]) if company is not None else self.none()

    def for_user(self, user):
        """Rows in any company the user is a member of. Anonymous/inactive -> nothing. Superusers still need a membership --
        cross-company access for staff is an explicit decision made by passing company ids, never implicit."""
        from core.permissions.scope import user_company_ids

        return self.for_company_ids(user_company_ids(user))


class CompanyQuerySet(CompanyQuerySetMixin, models.QuerySet):
    pass


class CompanyScopedModel(models.Model):
    """Abstract: one owning company (``company`` FK, required)."""

    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="%(app_label)s_%(class)s_set",
                                verbose_name="company", db_index=True)
    company_lookup = "company"
    objects = CompanyQuerySet.as_manager()

    class Meta:
        abstract = True


class MultiCompanyQuerySet(CompanyQuerySet):
    company_lookup = "companies"


class MultiCompanyScopedModel(models.Model):
    """Abstract: visible to several companies (``companies`` M2M)."""

    companies = models.ManyToManyField("core.Company", related_name="%(app_label)s_%(class)s_set", verbose_name="companies")
    company_lookup = "companies"
    objects = MultiCompanyQuerySet.as_manager()

    class Meta:
        abstract = True


def object_company_ids(obj) -> set:
    """Company ids an instance belongs to (empty set when the model is not company scoped)."""
    lookup = getattr(obj, "company_lookup", None)
    if lookup == "company":
        return {obj.company_id} if getattr(obj, "company_id", None) else set()
    if lookup == "companies" and obj.pk:
        return set(obj.companies.values_list("pk", flat=True))
    return set()


# --------------------------------------------------------------------------------------------------------------------- soft delete
ALIVE, DELETED, EVERYTHING = "alive", "deleted", "all"


class SoftDeleteQuerySetMixin:
    """Queryset ``delete()`` is ALWAYS a soft delete (per instance, so signals/audit fire). There is deliberately no queryset-level
    hard delete: physical removal is only ``instance.hard_delete()``, and only from the alive/all managers' instances."""

    _sd_mode = ALIVE

    def _clone(self):
        clone = super()._clone()
        clone._sd_mode = self._sd_mode
        return clone

    def delete(self):
        if self._sd_mode == DELETED:
            return 0, {}
        user = context.current_user()
        count = 0
        with transaction.atomic():
            for obj in list(self.filter(deleted_at__isnull=True)):
                obj._soft_delete(user)
                count += 1
        return count, ({self.model._meta.label: count} if count else {})

    delete.alters_data = True
    delete.queryset_only = True

    def restore(self):
        count = 0
        with transaction.atomic():
            for obj in list(self.filter(deleted_at__isnull=False)):
                obj.restore()
                count += 1
        return count

    restore.alters_data = True

    def _raw_delete(self, using):  # pragma: no cover - guard
        raise ImproperlyConfigured("Physical deletes of soft-delete models go through instance.hard_delete() only.")


class SoftDeleteQuerySet(SoftDeleteQuerySetMixin, models.QuerySet):
    pass


class _Manager(models.Manager):
    mode = ALIVE
    queryset_class = SoftDeleteQuerySet

    def get_queryset(self):
        qs = self.queryset_class(self.model, using=self._db)
        qs._sd_mode = self.mode
        if self.mode == ALIVE:
            return qs.filter(deleted_at__isnull=True)
        if self.mode == DELETED:
            return qs.filter(deleted_at__isnull=False)
        return qs


class SoftDeleteManager(_Manager):
    mode = ALIVE


class DeletedManager(_Manager):
    mode = DELETED


class GlobalManager(_Manager):
    mode = EVERYTHING


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField("deleted at", null=True, blank=True, editable=False, db_index=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="deleted by")

    objects = SoftDeleteManager()            # FIRST => default manager => never exposes deleted rows
    deleted_objects = DeletedManager()
    global_objects = GlobalManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def _soft_delete(self, user=None, at=None) -> None:
        if self.deleted_at is not None:
            return
        self.deleted_at = at or timezone.now()
        self.deleted_by = user if getattr(user, "is_authenticated", False) else None
        type(self).global_objects.filter(pk=self.pk).update(deleted_at=self.deleted_at, deleted_by=self.deleted_by)
        post_soft_delete.send(sender=type(self), instance=self, user=self.deleted_by)

    def delete(self, using=None, keep_parents=False):
        with transaction.atomic():
            self._soft_delete(context.current_user())
        return 1, {self._meta.label: 1}

    delete.alters_data = True

    def restore(self) -> None:
        if self.deleted_at is None:
            return
        self.deleted_at = None
        self.deleted_by = None
        type(self).global_objects.filter(pk=self.pk).update(deleted_at=None, deleted_by=None)
        post_restore.send(sender=type(self), instance=self, user=context.current_user())

    restore.alters_data = True

    def hard_delete(self, using=None, keep_parents=False):
        """Physically delete this row (explicit opt-in; cascades follow the FK ``on_delete`` rules)."""
        return super().delete(using=using, keep_parents=keep_parents)

    hard_delete.alters_data = True


class CompanyScopedSoftDeleteQuerySet(SoftDeleteQuerySetMixin, CompanyQuerySet):
    pass


class _CompanySoftManager(_Manager):
    queryset_class = CompanyScopedSoftDeleteQuerySet


class _CompanySoftDeleted(_CompanySoftManager):
    mode = DELETED


class _CompanySoftGlobal(_CompanySoftManager):
    mode = EVERYTHING


class CompanyScopedSoftDeleteModel(CompanyScopedModel, SoftDeleteModel):
    """Company scoped + soft delete with correct manager ordering (alive-only default manager)."""

    objects = _CompanySoftManager()
    deleted_objects = _CompanySoftDeleted()
    global_objects = _CompanySoftGlobal()

    class Meta:
        abstract = True


# --------------------------------------------------------------------------------------------------------------------- audit marker
class AuditTrackedModel(models.Model):
    """Opt-in: instances of subclasses get create/update/delete/restore events with old/new field snapshots in ``AuditLog``.
    ``audit_exclude_fields`` lists fields never snapshotted (secrets are also dropped by name, see ``core.audit.redaction``)."""

    audit_exclude_fields: tuple = ()

    class Meta:
        abstract = True
