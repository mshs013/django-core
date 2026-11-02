from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only event log. ``changes`` holds ``{field: [old, new]}`` for the changed fields and ``snapshot`` every field of the
    record after the event (rows written before snapshots existed have none); secrets are already removed (see core.audit)."""

    CREATE, UPDATE, DELETE, RESTORE = "create", "update", "delete", "restore"
    LOGIN, LOGIN_FAILED, LOGOUT = "login", "login_failed", "logout"
    PERMISSION = "permission"
    BACKUP, ARCHIVE = "backup", "archive"
    APPROVAL, REPORT_RUN, SAVED_SEARCH = "approval", "report_run", "saved_search"
    OTHER = "other"
    EVENTS = [(CREATE, "Create"), (UPDATE, "Update"), (DELETE, "Delete"), (RESTORE, "Restore"), (LOGIN, "Login"),
              (LOGIN_FAILED, "Login failed"), (LOGOUT, "Logout"), (PERMISSION, "Permission change"), (BACKUP, "Backup"),
              (ARCHIVE, "Archive"), (APPROVAL, "Approval"), (REPORT_RUN, "Report run"), (SAVED_SEARCH, "Saved search"),
              (OTHER, "Other")]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    event = models.CharField(max_length=20, choices=EVENTS, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user_repr = models.CharField("by", max_length=200, blank=True)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    model_label = models.CharField(max_length=100, blank=True, db_index=True)
    object_pk = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField("record", max_length=300, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    snapshot = models.JSONField(null=True, blank=True, help_text="Every field of the record after the event (secrets removed).")
    message = models.CharField(max_length=500, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["model_label", "object_pk"], name="core_audit_object_idx"),
                   models.Index(fields=["company", "-created_at"], name="core_audit_company_idx")]

    def __str__(self) -> str:
        return f"{self.get_event_display()} {self.model_name} {self.object_pk}".strip()

    @property
    def model_name(self) -> str:
        """The model's verbose name (``"User"``) instead of its label (``"core.User"``)."""
        from core.audit.display import verbose_model_name

        return verbose_model_name(self.model_label)

    model_name.fget.short_description = "Model"

    def changes_display(self):
        """All field values with the changed ones highlighted (or, for older rows, the "New / Old" changes) -- safe HTML."""
        from core.audit.display import render_changes

        return render_changes(self)

    changes_display.short_description = "Changes"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit rows are append-only.")
