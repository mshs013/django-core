"""Row-level access given to a user on top of model permissions (see ``core.permissions.access``).

``ObjectAccess``: the rows of an *access source* (e.g. buyer H&M) a user may work with; a row with no ``object_id`` means "all of
them". Models scoped by that source (e.g. orders by their buyer) then show only matching rows, and the source's dropdowns offer
only those rows. A user with no row at all for a source sees none of them (superusers are never limited).

``UserAccess``: for models registered with an ``owner_field``, a user sees their own records plus those of the users listed here;
a row with no ``target`` means "everyone's"."""
from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ObjectAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="object_access")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField(null=True, blank=True, help_text="Empty = all rows of this kind.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "object access"
        verbose_name_plural = "object access"
        constraints = [
            models.UniqueConstraint(fields=["user", "content_type", "object_id"], name="core_objectaccess_unique_row"),
            models.UniqueConstraint(fields=["user", "content_type"], condition=models.Q(object_id__isnull=True),
                                    name="core_objectaccess_unique_all"),
        ]
        indexes = [models.Index(fields=["user", "content_type"], name="core_objectaccess_user_ct")]

    def __str__(self) -> str:
        return f"{self.user} → {self.content_type.model} {self.object_id or 'all'}"


class UserAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_access")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+",
                               help_text="Empty = everyone's records.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "user access"
        verbose_name_plural = "user access"
        constraints = [
            models.UniqueConstraint(fields=["user", "target"], name="core_useraccess_unique"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(target__isnull=True), name="core_useraccess_unique_all"),
            models.CheckConstraint(condition=~models.Q(user=models.F("target")), name="core_useraccess_not_self"),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.target or 'everyone'}"
