"""Choice widgets for many-to-many fields: a checkbox list with a filter box, and the same list grouped under headings (e.g.
permissions grouped by app › model, each heading with an "all" toggle). Pick one per field with ``register_resource(...,
m2m_widgets={"field": "checkbox"})`` or site-wide with ``CORE_M2M_WIDGET``."""
from __future__ import annotations

from django import forms


class CheckboxList(forms.CheckboxSelectMultiple):
    """Checkboxes in a scrollable box with a filter input (long lists stay usable)."""

    template_name = "core/widgets/checkbox_list.html"


class GroupedCheckboxList(CheckboxList):
    """``CheckboxList`` under headings. ``group_of`` maps an option value (str) to its heading; options without one go last."""

    template_name = "core/widgets/grouped_checkboxes.html"

    def __init__(self, *args, group_of: dict | None = None, code_of: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_of = group_of or {}
        self.code_of = code_of or {}          # option value (str) -> a code shown after the label (and matched by the filter)

    def optgroups(self, name, value, attrs=None):
        flat = [option for _group, options, _index in super().optgroups(name, value, attrs) for option in options]
        groups: dict[str, list] = {}
        for option in flat:
            option["code"] = self.code_of.get(str(option["value"]), "")
            groups.setdefault(self.group_of.get(str(option["value"]), "Other"), []).append(option)
        return [(label, options, index) for index, (label, options) in enumerate(groups.items())]


M2M_WIDGETS = {"select": forms.SelectMultiple, "checkbox": CheckboxList}


def m2m_widget(kind: str):
    try:
        return M2M_WIDGETS[kind]
    except KeyError:
        raise ValueError(f"m2m widget must be one of {sorted(M2M_WIDGETS)}, got {kind!r}") from None


def permission_choices_field(queryset, *, label="Permissions", required=False, help_text=""):
    """A ``ModelMultipleChoiceField`` over ``auth.Permission`` rendered as checkboxes grouped by "App › Model"."""
    queryset = queryset.select_related("content_type").order_by("content_type__app_label", "content_type__model", "codename")
    group_of, code_of = {}, {}
    for perm in queryset:
        ct = perm.content_type
        model = ct.model_class()
        model_name = model._meta.verbose_name_plural if model else ct.model
        group_of[str(perm.pk)] = f"{ct.app_label} › {str(model_name).capitalize()}"
        code_of[str(perm.pk)] = f"{ct.app_label}.{perm.codename}"
    field = forms.ModelMultipleChoiceField(queryset=queryset, required=required, label=label, help_text=help_text,
                                           widget=GroupedCheckboxList(group_of=group_of, code_of=code_of))
    field.label_from_instance = lambda perm: perm.name             # the heading already names the app and model
    return field
