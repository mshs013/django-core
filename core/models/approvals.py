"""Approval models + mixins.

Permission-layer inspection (spec section 11): Django groups/permissions + guardian + company membership give *who may do X on
object O in company C*, but have no notion of approval type / step / chain. Path taken: PARTIAL -> extend. ``core.permissions``
(fail-closed, cached, company aware) is reused for every authorization decision; only the chain/step/request models are new.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .base import AuditTrackedModel, CreatedInfoModel


def unique_code(model, name: str, pk=None, field: str = "code", max_length: int = 60) -> str:
    """Internal slug for approval types/steps (no longer typed by users): code-registered chains still match on it."""
    from django.utils.text import slugify

    base = (slugify(name) or "item")[:max_length - 4]
    code, n = base, 1
    while model.objects.filter(**{field: code}).exclude(pk=pk).exists():
        n += 1
        code = f"{base}-{n}"
    return code


class AuditedModel(AuditTrackedModel):
    """Abstract approvable target: audit snapshots + approval state. ``is_approval``/``approved_at`` are set ONLY by the engine when
    the final step approves."""

    approval_target = ""                       # key registered with core.approvals.register_target
    is_confirm = models.BooleanField("sent for approval", default=False, editable=False)
    is_approval = models.BooleanField("approved", default=False, editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True


class CancelableModel(models.Model):
    """Abstract, opt-in. Only models inheriting it get a Cancel action."""

    is_cancel = models.BooleanField("cancelled", default=False, editable=False)
    canceled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                    related_name="+")
    canceled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True


class ApprovalStep(CreatedInfoModel):
    """A reusable step: who may decide (permission codename), an optional auto-skip rule (name registered in code) and how the
    decision looks in *Awaiting my approval* (labels of the two choices, the button text, whether Observation is offered)."""

    code = models.SlugField(max_length=60, unique=True, editable=False)
    name = models.CharField(max_length=120)
    display_name = models.CharField(max_length=120, blank=True, help_text="Shown to approvers; empty = the name.")
    pass_label = models.CharField("pass label", max_length=40, default="Approve")
    fail_label = models.CharField("fail label", max_length=40, default="Reject")
    button_text = models.CharField(max_length=40, default="Submit")
    sequence = models.PositiveIntegerField(default=0, help_text="Default order of the step in lists.")
    allow_observation = models.BooleanField("offer Observation", default=False,
                                            help_text="Approvers may pass the step with an observation: the creator is mailed it and "
                                                      "the request moves on to the next step.")
    signature_label = models.CharField(max_length=60, blank=True,
                                       help_text="Heading of this step's signature on a printed record, e.g. Checked By; empty = the name.")
    permission = models.CharField("required permission", max_length=150, blank=True,
                                  help_text="Codename (app_label.codename) a user needs to decide this step; empty = superusers only.")
    skip_rule = models.CharField("auto-skip rule", max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sequence", "name", "pk")

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = unique_code(type(self), self.name, self.pk)
        super().save(*args, **kwargs)


class ApprovalType(CreatedInfoModel, AuditTrackedModel):
    """An approval chain. Bound to a model either in code (``register_target``) or here, by choosing the model in *Applies to*:
    every record of that model then gets the approval panel, with no code."""

    RETURN_TO_SUBMITTER, PREVIOUS_STEP = "return_to_submitter", "previous_step"
    REJECT_MODES = [(RETURN_TO_SUBMITTER, "Return to submitter"), (PREVIOUS_STEP, "Back to previous approved step")]
    NOTIFY_NONE, NOTIFY_APPROVED, NOTIFY_REJECTED, NOTIFY_BOTH = "", "approved", "rejected", "both"
    NOTIFY_ON = [(NOTIFY_NONE, "Nobody else"), (NOTIFY_APPROVED, "When approved"), (NOTIFY_REJECTED, "When rejected"),
                 (NOTIFY_BOTH, "When approved or rejected")]

    code = models.SlugField(max_length=60, unique=True, editable=False)
    name = models.CharField(max_length=120)
    queue_fields = models.CharField("queue columns", max_length=400, blank=True,
                                    help_text="Record fields shown in Awaiting my approval, comma separated (e.g. budget_no, buyer, style).")
    reference_field = models.CharField("reference column", max_length=80, blank=True,
                                       help_text="Field that names the record in observation mails (e.g. budget_no); empty = the record name.")
    reject_mode = models.CharField(max_length=24, choices=REJECT_MODES, default=RETURN_TO_SUBMITTER)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Empty = usable in every company.")
    permission = models.CharField("type permission", max_length=150, blank=True,
                                  help_text="Optional permission required to see/act on any request of this type.")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                     verbose_name="applies to",
                                     help_text="The records that need this approval. Leave empty for chains bound in code.")
    notify_on = models.CharField("tell others", max_length=10, choices=NOTIFY_ON, default=NOTIFY_NONE, blank=True,
                                 help_text="Notify the users/groups below when a request finishes. The submitter is always told.")
    notify_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    notify_groups = models.ManyToManyField(Group, blank=True, related_name="+")
    notify_email = models.BooleanField("also e-mail", default=False,
                                       help_text="Send the finish notices (submitter and the people above) by e-mail as well.")
    step_adders = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+", verbose_name="may add steps",
                                         help_text="People who may add an extra step (with its approver) to a pending request of this "
                                                   "type. Holders of core.add_step_to_request may do it on every type.")
    EXPORT_DEFAULT, EXPORT_BOTH, EXPORT_PDF, EXPORT_XLSX, EXPORT_NONE = "", "both", "pdf", "xlsx", "none"
    EXPORT_BUTTONS = [(EXPORT_DEFAULT, "Page default"), (EXPORT_BOTH, "PDF and Excel"), (EXPORT_PDF, "PDF only"),
                      (EXPORT_XLSX, "Excel only"), (EXPORT_NONE, "None")]
    WHEN_DEFAULT, WHEN_ALWAYS, WHEN_APPROVED = "", "always", "approved"
    EXPORT_WHEN = [(WHEN_DEFAULT, "Page default"), (WHEN_ALWAYS, "All the time"), (WHEN_APPROVED, "Only when approved")]
    export_buttons = models.CharField("export buttons", max_length=8, choices=EXPORT_BUTTONS, default=EXPORT_DEFAULT, blank=True,
                                      help_text="PDF / Excel buttons on a record's detail page. Page default = as registered in code "
                                                "(PDF and Excel unless the page says otherwise).")
    export_when = models.CharField("show export buttons", max_length=8, choices=EXPORT_WHEN, default=WHEN_DEFAULT, blank=True,
                                   help_text="Page default = as registered in code (all the time unless the page says otherwise).")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "pk")

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = unique_code(type(self), self.name, self.pk)
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import FieldDoesNotExist, ValidationError

        model = self.content_type.model_class() if self.content_type_id else None
        if model is None:
            return
        for name, value in (("queue_fields", self.queue_field_names()), ("reference_field", [self.reference_field] if self.reference_field else [])):
            for f in value:
                try:
                    model._meta.get_field(f)
                except FieldDoesNotExist:
                    raise ValidationError({name: f"“{f}” is not a field of {model._meta.verbose_name}."}) from None

    def notifies(self, outcome: str) -> bool:
        return self.notify_on in (outcome, self.NOTIFY_BOTH)

    def export_formats(self) -> tuple[str, ...] | None:
        """Detail-page export formats chosen here, or None for the page default."""
        return {self.EXPORT_BOTH: ("pdf", "xlsx"), self.EXPORT_PDF: ("pdf",), self.EXPORT_XLSX: ("xlsx",),
                self.EXPORT_NONE: ()}.get(self.export_buttons)

    def queue_field_names(self) -> list[str]:
        return [f.strip() for f in (self.queue_fields or "").split(",") if f.strip()]


class ApprovalTypeStep(AuditTrackedModel):
    approval_type = models.ForeignKey(ApprovalType, on_delete=models.CASCADE, related_name="type_steps")
    step = models.ForeignKey(ApprovalStep, on_delete=models.PROTECT, related_name="type_steps")
    position = models.PositiveIntegerField("sequence")          # deliberately NOT unique: reordering swaps values
    is_final = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True, help_text="Disable instead of deleting; disabled steps are skipped and logged.")
    permission = models.CharField("step permission scope", max_length=150, blank=True,
                                  help_text="Overrides the step's permission for this chain when set.")
    approver_group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approver_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="approver_steps",
                                            help_text="People who may decide this step (in addition to the group and the permission).")
    condition = models.JSONField("only when", default=dict, blank=True,
                                 help_text='Run this step only for matching records, else it is skipped. Example: '
                                           '{"field": "price", "op": "gte", "value": "10000"} -- or a list of such rows (all must match). '
                                           "Fields are the page's filterable fields. Empty = always.")

    class Meta:
        ordering = ("approval_type_id", "position", "pk")
        constraints = [
            models.UniqueConstraint(fields=["approval_type"], condition=models.Q(is_final=True), name="core_approvaltypestep_one_final"),
            models.UniqueConstraint(fields=["approval_type", "step"], name="core_approvaltypestep_unique_step"),
        ]

    def __str__(self) -> str:
        return f"{self.approval_type} #{self.position}: {self.step}"

    def save(self, *args, **kwargs):
        if not self.condition:
            self.condition = {}                       # an empty form box arrives as None; the column is NOT NULL
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        from core.approvals.conditions import normalize, validate
        from core.approvals.errors import ApprovalError

        try:
            tree = normalize(self.condition)
            if tree.get("items") and self.is_final:
                raise ApprovalError("The final step always runs; it cannot have a condition.")
            atype = self.approval_type if self.approval_type_id else None
            model = atype.content_type.model_class() if atype is not None and atype.content_type_id else None
            if tree.get("items") and model is not None:
                self.condition = validate(model, tree)
        except ApprovalError as exc:
            raise ValidationError({"condition": str(exc)}) from None


class ApprovalRequest(CreatedInfoModel):
    PENDING, APPROVED, REJECTED, CANCELLED = "pending", "approved", "rejected", "cancelled"
    STATUSES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    approval_type = models.ForeignKey(ApprovalType, on_delete=models.PROTECT, related_name="requests")
    target_key = models.CharField(max_length=80, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField("record", max_length=300, blank=True)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    round = models.PositiveIntegerField(default=1)
    current_sequence = models.PositiveIntegerField(default=0, help_text="Sequence of the step awaiting a decision (0 = none).")
    chain = models.JSONField("frozen chain snapshot", default=list)
    extra_steps = models.JSONField("per-instance extra steps", default=list, blank=True)
    reject_mode = models.CharField(max_length=24, choices=ApprovalType.REJECT_MODES, default=ApprovalType.RETURN_TO_SUBMITTER)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [models.UniqueConstraint(fields=["content_type", "object_id"], condition=models.Q(status="pending"),
                                               name="core_approvalrequest_one_pending_per_object")]
        indexes = [models.Index(fields=["content_type", "object_id"], name="core_approvalreq_object_idx"),
                   models.Index(fields=["company", "status"], name="core_approvalreq_company_idx")]

    def __str__(self) -> str:
        return f"{self.object_repr or self.object_id} ({self.get_status_display()})"

    def step_at(self, sequence: int) -> dict | None:
        return next((s for s in self.chain if s["sequence"] == sequence), None)

    @property
    def request_page(self):
        """Link to the request page (chain, history, decisions, add a step) for the read-only admin view."""
        from django.urls import reverse
        from django.utils.html import format_html

        return format_html('<a href="{}">{}</a>', reverse("core:approval_detail", args=[self.pk]), "Open request page")

    request_page.fget.short_description = "Request page"

    @property
    def steps_summary(self) -> str:
        """The frozen chain in order, e.g. "Merchandiser → QA check (added) → [Manager]"; the step awaiting a decision in brackets."""
        names = []
        for s in sorted(self.chain, key=lambda s: s["sequence"]):
            name = (s.get("name") or s.get("code") or "?") + (" (added)" if s.get("kind") == "extra" else "")
            names.append(f"[{name}]" if self.status == self.PENDING and s["sequence"] == self.current_sequence else name)
        return " → ".join(names)

    steps_summary.fget.short_description = "Steps"


class Approval(models.Model):
    """Append-only history row of an approval request."""

    SUBMIT, APPROVE, REJECT, SKIP, CANCEL, RESUBMIT, ADD_STEP = "submit", "approve", "reject", "skip", "cancel", "resubmit", "add_step"
    OBSERVE = "observe"
    ACTIONS = [(SUBMIT, "Submitted"), (APPROVE, "Approved"), (REJECT, "Rejected"), (SKIP, "Skipped"), (CANCEL, "Cancelled"),
               (RESUBMIT, "Resubmitted"), (ADD_STEP, "Step added"), (OBSERVE, "Observation")]

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="history")
    round = models.PositiveIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=0)
    step_name = models.CharField(max_length=120, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("created_at", "pk")
        permissions = [("add_step_to_request", "Can inject an extra step into a pending approval request")]

    def __str__(self) -> str:
        return f"{self.get_action_display()} {self.step_name}".strip()
