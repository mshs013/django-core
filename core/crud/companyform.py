"""Company form: the parent selector shows the hierarchy (parents first, their children indented underneath and disabled, because only
a top-level company can be a parent). Server-side validation lives in ``Company.clean`` -- the disabled options are only a hint."""
from __future__ import annotations

from django import forms
from django.db.models import F, Q
from django.db.models.functions import Coalesce

from core.crud.forms import build_form_class


class HierarchySelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None and instance.parent_id:
            option["label"] = f"   └ {instance.name}"
            option["attrs"]["disabled"] = True                       # a child cannot be a parent
        return option


def company_form_class(cfg, ctx):
    from core.models import Company

    base = build_form_class(cfg, ctx)
    everything = bool(getattr(ctx.user, "is_superuser", False))

    class CompanyForm(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            field = self.fields["parent"]
            qs = Company.objects.all() if everything else Company.objects.filter(pk__in=list(ctx.company_ids))
            if self.instance.parent_id:
                qs = Company.objects.filter(Q(pk__in=qs.values("pk")) | Q(pk=self.instance.parent_id))   # keep an out-of-scope current parent
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)                                                       # never offer itself
            field.queryset = qs.order_by(Coalesce("parent_id", "pk"), F("parent_id").asc(nulls_first=True), "name")
            field.widget = HierarchySelect(attrs=field.widget.attrs)
            field.widget.choices = field.choices
            field.empty_label = "— none (top-level company) —"
            field.help_text = "Only top-level companies can be parents; their children are shown indented."

    return CompanyForm
