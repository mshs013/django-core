"""Internal mail (Gmail-like): a message is written once; every participant gets their own ``Mailbox`` row holding *their* state
(read, starred, archived, trashed, labels). Labels are the folders; filters file incoming mail automatically."""
from __future__ import annotations

from django.conf import settings
from django.db import models


class MailMessage(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                               help_text="Empty = sent by the system (rules, approvals).")
    sender_name = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    thread = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                               help_text="First message of the conversation (empty on that first message).")
    in_reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    url = models.CharField("related link", max_length=300, blank=True)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                help_text="Company the message comes from (the sender's active company, or the record's company "
                                          "for system mail). Mail itself is not company scoped: it shows in every company.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return self.subject or "(no subject)"

    @property
    def thread_id_or_self(self) -> int:
        return self.thread_id or self.pk


class MailAttachment(models.Model):
    message = models.ForeignKey(MailMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.ForeignKey("core.StoredFile", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ("pk",)


class MailLabel(models.Model):
    COLORS = [(c, c.capitalize()) for c in ("blue", "azure", "green", "lime", "yellow", "orange", "red", "pink", "purple", "secondary")]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_labels")
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=12, choices=COLORS, default="blue")

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="core_maillabel_unique_name")]

    def __str__(self) -> str:
        return self.name


class Mailbox(models.Model):
    TO, CC, BCC, FROM = "to", "cc", "bcc", "from"
    ROLES = [(TO, "To"), (CC, "Cc"), (BCC, "Bcc"), (FROM, "From")]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mailbox")
    message = models.ForeignKey(MailMessage, on_delete=models.CASCADE, related_name="copies")
    role = models.CharField(max_length=4, choices=ROLES)
    is_read = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)
    is_archived = models.BooleanField("skip inbox", default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField("deleted forever", default=False)
    labels = models.ManyToManyField(MailLabel, blank=True, related_name="items")

    class Meta:
        ordering = ("-message__created_at", "-pk")
        constraints = [models.UniqueConstraint(fields=["owner", "message", "role"], name="core_mailbox_unique_copy")]
        indexes = [models.Index(fields=["owner", "role", "is_read"], name="core_mailbox_owner_idx")]

    @property
    def is_sent_copy(self) -> bool:
        return self.role == self.FROM


class MailFilter(models.Model):
    """Applied to each incoming message, in order: when every filled-in criterion matches, the actions run."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_filters")
    name = models.CharField(max_length=80)
    from_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+", verbose_name="from")
    from_system = models.BooleanField("from the system", default=False)
    subject_contains = models.CharField(max_length=120, blank=True)
    body_contains = models.CharField("text contains", max_length=120, blank=True)
    label = models.ForeignKey(MailLabel, null=True, blank=True, on_delete=models.CASCADE, related_name="filters",
                              verbose_name="apply label")
    mark_read = models.BooleanField(default=False)
    star = models.BooleanField(default=False)
    skip_inbox = models.BooleanField("skip the inbox (archive)", default=False)
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("position", "pk")

    def __str__(self) -> str:
        return self.name
