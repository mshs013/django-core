from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from .base import CreatedInfoModel


class Notification(models.Model):
    INFO, SUCCESS, WARNING, DANGER = "info", "success", "warning", "danger"
    LEVELS = [(INFO, "Info"), (SUCCESS, "Success"), (WARNING, "Warning"), (DANGER, "Danger")]

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=300, blank=True)
    level = models.CharField(max_length=10, choices=LEVELS, default=INFO)
    kind = models.CharField(max_length=40, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["recipient", "is_read", "-created_at"], name="core_notif_recipient_idx")]

    def __str__(self) -> str:
        return self.title

    def mark_read(self) -> None:
        if not self.is_read:
            self.is_read, self.read_at = True, timezone.now()
            self.save(update_fields=["is_read", "read_at"])


class NotificationRule(CreatedInfoModel):
    """A notification a superuser builds in the app: *when* <event> happens to a record of <model> (optionally only when the
    record matches a condition), tell <recipients> with <title/body>. Delivered to the top-bar bell (and e-mail if asked)."""

    CREATED, UPDATED, DELETED = "created", "updated", "deleted"
    SUBMITTED, APPROVED, REJECTED = "approval_submitted", "approval_approved", "approval_rejected"
    EVENTS = [(CREATED, "Record created"), (UPDATED, "Record changed"), (DELETED, "Record deleted"),
              (SUBMITTED, "Sent for approval"), (APPROVED, "Approval completed (approved)"), (REJECTED, "Approval rejected")]
    APPROVAL_EVENTS = (SUBMITTED, APPROVED, REJECTED)

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+", verbose_name="applies to")
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = records of every company.")
    event = models.CharField("when", max_length=24, choices=EVENTS, default=CREATED)
    watch_fields = models.CharField("only when these change", max_length=300, blank=True,
                                    help_text="“Record changed” only: comma-separated field names; empty = any change.")
    condition = models.JSONField("only for records matching", default=dict, blank=True,
                                 help_text='Example: {"field": "status", "op": "eq", "value": "ready"} -- or a list of such rows (all '
                                           "must match). Fields are the page's filterable fields. Empty = every record.")
    notify_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+", verbose_name="users")
    notify_groups = models.ManyToManyField(Group, blank=True, related_name="+", verbose_name="groups")
    notify_creator = models.BooleanField("the record's creator", default=False)
    notify_submitter = models.BooleanField("the approval submitter", default=False, help_text="Approval events only.")
    notify_approvers = models.BooleanField("the approvers", default=False,
                                           help_text="Approval events only: everyone who approved a step of the request.")
    user_field = models.CharField("the user in field", max_length=100, blank=True,
                                  help_text="Name of a field of the record that points to a user (e.g. merchandiser).")
    notify_actor = models.BooleanField("also the person who did it", default=False)
    title = models.CharField(max_length=200, default="{model} {event}: {record}",
                             help_text="Placeholders: {record} {model} {event} {actor} {company} and any field name, e.g. {status}.")
    body = models.CharField(max_length=500, blank=True, help_text="Same placeholders as the title.")
    level = models.CharField(max_length=10, choices=Notification.LEVELS, default=Notification.INFO)
    send_in_app = models.BooleanField("show in the bell", default=True)
    send_mail = models.BooleanField("send as internal mail", default=False, help_text="Delivered to the recipients' Mail inbox.")
    send_email = models.BooleanField("also e-mail", default=False, help_text="A copy to their e-mail address (needs the server's e-mail set up).")

    class Meta:
        ordering = ("name", "pk")

    def __str__(self) -> str:
        return self.name

    def watched(self) -> list[str]:
        return [n.strip() for n in self.watch_fields.split(",") if n.strip()]

    def save(self, *args, **kwargs):
        if not self.condition:
            self.condition = {}
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        from core.notifications.rules import validate_rule

        errors = validate_rule(self)
        if errors:
            raise ValidationError(errors)
