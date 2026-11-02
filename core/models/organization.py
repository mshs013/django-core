from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.files.storage import private_storage

from .base import AuditTrackedModel, CreatedInfoModel, SoftDeleteModel, UpdatedInfoModel
from .constraints import CheckConstraintSkippedOnMySQL


class Company(CreatedInfoModel, UpdatedInfoModel, SoftDeleteModel, AuditTrackedModel):
    """A company. Two levels only: a *parent* (group) company and its child companies.

    ``is_parent`` is validated against the data: it must be ticked when the company has children and can never be ticked on a company
    that itself has a parent. Saving a child marks its parent as a parent company automatically."""

    is_parent = models.BooleanField("is a parent company", default=False, help_text="Tick for a group company that has child companies.")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children", verbose_name="parent company")
    name = models.CharField(max_length=255)
    abbreviation = models.CharField(max_length=50)
    legal_name = models.CharField(max_length=200, blank=True)
    tax_id = models.CharField("tax / registration no.", max_length=60, blank=True)
    address = models.TextField()
    factory_address = models.TextField(blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    fax = models.CharField(max_length=50, blank=True, default="")
    logo = models.ImageField(upload_to="companies/logos/", storage=private_storage, null=True, blank=True)
    website = models.URLField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "company"
        verbose_name_plural = "companies"
        ordering = ("name", "pk")
        constraints = [
            models.CheckConstraint(condition=~(models.Q(is_parent=True) & models.Q(parent__isnull=False)), name="core_company_parent_is_not_parent"),
            CheckConstraintSkippedOnMySQL(condition=~models.Q(parent=models.F("pk")), name="core_company_not_own_parent"),  # pk = AUTO_INCREMENT
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        has_children = bool(self.pk) and Company.objects.filter(parent_id=self.pk).exists()
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                errors["parent"] = "A company cannot be its own parent."
            elif self.parent.parent_id:                       # also rules out every cycle: a parent can never have a parent
                errors["parent"] = "Choose a top-level company as the parent (only two levels are supported)."
            elif has_children:
                errors["parent"] = "This company has child companies, so it cannot be placed under another company."
            if self.is_parent:
                errors["is_parent"] = "A company that has a parent cannot itself be a parent company."
        elif has_children and not self.is_parent:
            errors["is_parent"] = "Tick this: the company has child companies."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.parent_id:                                        # derived: a company with children is a parent company
            from core.audit.service import audited_update

            audited_update(Company.global_objects.filter(pk=self.parent_id, is_parent=False), message="became a parent company", is_parent=True)

    @property
    def hierarchy_label(self) -> str:
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name


class Department(CreatedInfoModel, UpdatedInfoModel, SoftDeleteModel, AuditTrackedModel):
    """Shared by every company (no company field)."""

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=["name"], condition=models.Q(deleted_at__isnull=True),
                                               name="core_department_unique_name")]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A department cannot be its own parent."})
            node = self.parent
            seen = set()
            while node is not None and node.pk not in seen:
                if node.pk == self.pk:
                    raise ValidationError({"parent": "This would create a cycle."})
                seen.add(node.pk)
                node = node.parent


class Designation(CreatedInfoModel, UpdatedInfoModel, SoftDeleteModel, AuditTrackedModel):
    """Shared by every company (no company field)."""

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=["name"], condition=models.Q(deleted_at__isnull=True),
                                               name="core_designation_unique_name")]

    def __str__(self) -> str:
        return self.name
