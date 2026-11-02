"""Admin forms whose *Applies to* field offers only models that have a list page (registered resources), labelled like the menu:
approval types and notification rules."""
from __future__ import annotations

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.db.models import Case, Value, When

from core.crud.forms import build_form_class


class ResourceModelChoiceField(forms.ModelChoiceField):
    labels: dict = {}

    def label_from_instance(self, obj):
        return self.labels.get(obj.pk) or str(obj)


APPROVAL_FIELDS = ("is_confirm", "is_approval", "approved_at")


def is_approvable(model) -> bool:
    """A model can carry an approval chain only when it has the approval state columns (see ``core.models.AuditedModel``)."""
    names = {f.name for f in model._meta.get_fields()}
    return all(n in names for n in APPROVAL_FIELDS)


def limit_to_resources(form, name: str = "content_type", *, exclude=(), empty_label: str | None = "—", only=None) -> None:
    from core.crud.registry import all_resources

    resources = [c for c in all_resources() if c.model not in set(exclude) and (only is None or only(c.model))]
    cts = ContentType.objects.get_for_models(*[c.model for c in resources], for_concrete_models=False) if resources else {}
    labels = {cts[c.model].pk: c.label_plural for c in resources if c.model in cts}
    old = form.fields[name]
    by_label = sorted(labels, key=lambda pk: labels[pk].lower())                 # in the order of the names shown, not app/model
    order = Case(*[When(pk=pk, then=Value(i)) for i, pk in enumerate(by_label)]) if by_label else "pk"
    field = ResourceModelChoiceField(queryset=ContentType.objects.filter(pk__in=by_label).order_by(order),
                                     required=old.required, label=old.label, help_text=old.help_text, empty_label=empty_label)
    field.labels = labels
    field.widget.attrs.update(old.widget.attrs)
    form.fields[name] = field


def approver_people(ctx):
    """Users that may be picked as approvers: everyone active for a superuser, else the members of the editor's companies."""
    from django.contrib.auth import get_user_model

    people = get_user_model().objects.filter(is_active=True).order_by("full_name", "email")
    if not getattr(ctx.user, "is_superuser", False):
        people = people.filter(profile__companies__in=ctx.member_ids).distinct()
    return people


def approval_type_form_class(cfg, ctx):
    from core.crud.widgets import CheckboxList
    from core.models import ApprovalRequest, ApprovalStep, ApprovalType

    base = build_form_class(cfg, ctx)

    class ApprovalTypeForm(base):
        """Tabs: the type, its steps, an Approvers tab (who may add steps to its requests; for a saved type also several approver
        users per step), and the detail page's PDF / Excel buttons."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            limit_to_resources(self, exclude=(ApprovalType, ApprovalStep, ApprovalRequest), empty_label="— set in code —",
                              only=is_approvable)
            self.step_fields = {}
            people = approver_people(ctx)
            adders = forms.ModelMultipleChoiceField(queryset=people, required=False, widget=CheckboxList, label="May add steps",
                                                    help_text=ApprovalType._meta.get_field("step_adders").help_text)
            adders.label_from_instance = lambda u: f"{u.full_name or u.email} · {u.email}"
            self.fields["step_adders"] = adders
            if self.instance.pk:
                self.initial["step_adders"] = list(self.instance.step_adders.values_list("pk", flat=True))
                for n, step in enumerate(self.instance.type_steps.select_related("step").order_by("position", "pk"), start=1):
                    name = f"approvers_{step.pk}"
                    field = forms.ModelMultipleChoiceField(queryset=people, required=False, widget=CheckboxList,
                                                           label=f"{n}. {step.step.name}" + (" (final)" if step.is_final else ""))
                    field.label_from_instance = lambda u: f"{u.full_name or u.email} · {u.email}"
                    self.fields[name] = field
                    self.initial[name] = list(step.approver_users.values_list("pk", flat=True))
                    self.step_fields[name] = step
            self.tabs = (("Approval type", ()), ("Steps", ("@steps",)), ("Approvers", ("step_adders", *self.step_fields)),
                         ("Print & export", ("export_buttons", "export_when")))
            if self.is_bound:
                self._errors = None          # the base form's styling validated it before the approver fields existed

        def save(self, commit=True):
            obj = super().save(commit=commit)
            offered = set(self.fields["step_adders"].queryset.values_list("pk", flat=True))
            hidden = set(obj.step_adders.exclude(pk__in=offered).values_list("pk", flat=True))   # people the editor cannot see
            obj.step_adders.set(hidden | {u.pk for u in self.cleaned_data.get("step_adders") or ()})
            for name, step in self.step_fields.items():
                offered = set(self.fields[name].queryset.values_list("pk", flat=True))
                hidden = set(step.approver_users.exclude(pk__in=offered).values_list("pk", flat=True))
                step.approver_users.set(hidden | {u.pk for u in self.cleaned_data.get(name) or ()})
            return obj

    return ApprovalTypeForm


def notification_rule_form_class(cfg, ctx):
    from core.models import Notification, NotificationRule

    base = build_form_class(cfg, ctx)

    class NotificationRuleForm(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            limit_to_resources(self, exclude=(Notification, NotificationRule))      # "—" first: no model is picked silently

    return NotificationRuleForm


def validate_step_conditions(obj, ctx) -> None:
    """``after_save`` hook (inside the save transaction): check every step condition against the chosen model now that the steps
    and the model are both known -- a new type's steps cannot see its model in their own ``clean()``. Fails the whole save."""
    from core.approvals.conditions import normalize, validate
    from core.approvals.errors import ApprovalError
    from core.crud.formsets import SaveFailed

    model = obj.content_type.model_class() if obj.content_type_id else None
    for ts in obj.type_steps.select_related("step"):
        try:
            tree = normalize(ts.condition)
            if not tree.get("items"):
                continue
            if ts.is_final:
                raise ApprovalError("the final step always runs; it cannot have a condition.")
            if model is not None:
                tree = validate(model, tree)
        except ApprovalError as exc:
            raise SaveFailed(f"Step “{ts.step.name}”: {exc}") from None
        if tree != ts.condition:
            type(ts).objects.filter(pk=ts.pk).update(condition=tree)
