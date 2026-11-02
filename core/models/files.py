from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .base import CreatedInfoModel


class StoredFile(CreatedInfoModel):
    """Metadata of a privately stored upload. The bytes live under CORE_PRIVATE_MEDIA_ROOT with a random name; the original
    name is only metadata (sanitised) and is never used as a path. Served only by an authorised view."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    storage_name = models.CharField(max_length=80, unique=True)
    original_name = models.CharField(max_length=200)
    content_type_header = models.CharField("media type", max_length=100, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    # Optional attachment target (generic) so the authorisation check can reuse the target's permission + company.
    target_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    target_id = models.CharField(max_length=64, blank=True)
    target = GenericForeignKey("target_ct", "target_id")
    attached = models.BooleanField(default=False, db_index=True, help_text="False until a saved record references the file (orphan cleanup).")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["target_ct", "target_id"], name="core_file_target_idx")]

    def __str__(self) -> str:
        return self.original_name
