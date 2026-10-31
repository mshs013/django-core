from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from .base import AuditTrackedModel, CreatedInfoModel, UpdatedInfoModel

VISIBILITY = [("private", "Private (only me)"), ("company", "Company (members with access to the target)"),
              ("public", "Public within the company")]
TARGET_TYPES = [("resource", "Resource"), ("report_source", "Report source")]


class SavedSearch(CreatedInfoModel, UpdatedInfoModel, AuditTrackedModel):
    """A validated filter tree + sort + page size for one registered target. ``definition`` is re-validated against the
    registry on every load; rows that no longer validate are skipped with a warning."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    target_type = models.CharField(max_length=20, choices=TARGET_TYPES, default="resource")
    target_key = models.CharField(max_length=100, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_searches")
    company = models.ForeignKey("core.Company", on_delete=models.CASCADE, related_name="saved_searches")
    visibility = models.CharField(max_length=10, choices=VISIBILITY, default="private")
    quick = models.CharField("quick search text", max_length=200, blank=True)
    definition = models.JSONField("filter tree", default=dict, blank=True)
    sort = models.JSONField("sort spec", default=list, blank=True)
    page_size = models.PositiveSmallIntegerField(default=25)
    pinned_to_menu = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField("standard search", default=False,
                                    help_text="Built by an administrator; offered to every user who can open the page, in every company.")

    class Meta:
        verbose_name = "saved search"
        verbose_name_plural = "saved searches"
        ordering = ("-is_system", "name", "pk")
        permissions = [("build_search", "Can build and save searches")]
        constraints = [
            models.UniqueConstraint(fields=["company", "owner", "target_type", "target_key", "slug"], name="core_savedsearch_unique_slug"),
            models.UniqueConstraint(fields=["owner", "company", "target_type", "target_key"], condition=models.Q(is_default=True),
                                    name="core_savedsearch_one_default"),
        ]
        indexes = [models.Index(fields=["company", "target_type", "target_key"], name="core_savedsearch_target_idx")]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140] or "search"
        super().save(*args, **kwargs)
