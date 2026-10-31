"""Role (auth group) form: name, permissions grouped by app › model, members. Staff may add and change roles but only hand out
permissions they hold themselves (``core.permissions.grants``); permissions and members they cannot see are kept on save."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from core.crud.forms import build_form_class
from core.crud.widgets import CheckboxList, permission_choices_field
from core.permissions.grants import can_grant_role, grantable_permissions


def role_form_class(cfg, ctx):
    User = get_user_model()
    base = build_form_class(cfg, ctx, fields=["name"])

    class RoleForm(base):
        tabs = (("Role", ("name",)), ("Permissions", ("permissions",)), ("Members", ("members",)))

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            user = ctx.user
            self.fields["permissions"] = permission_choices_field(
                grantable_permissions(user), help_text="" if user.is_superuser else "Only permissions you hold yourself are listed.")
            people = User.objects.filter(is_active=True).order_by("full_name", "email")
            if not user.is_superuser:
                people = people.filter(profile__companies__in=ctx.member_ids).distinct()
            self.fields["members"] = forms.ModelMultipleChoiceField(queryset=people, required=False, label="Members",
                                                                    widget=CheckboxList)
            self.fields["members"].label_from_instance = lambda u: f"{u.full_name or u.email} · {u.email}"
            pk = self.instance.pk
            if pk:
                self.initial["permissions"] = list(self.instance.permissions.values_list("pk", flat=True))
                self.initial["members"] = list(self.instance.user_set.values_list("pk", flat=True))
                if not can_grant_role(user, self.instance):
                    # the role holds permissions the editor lacks: adding people to it would hand those out
                    self.fields["members"].disabled = True
                    self.fields["members"].help_text = "This role has permissions you do not hold, so only a superuser can change its members."
            for name in ("permissions", "members"):
                self.fields[name].widget.attrs.pop("class", None)
            if self.is_bound:
                self._errors = None          # the base form's styling already validated it before these fields existed

        def save(self, commit=True):
            role = super().save(commit=commit)
            offered_perms = set(self.fields["permissions"].queryset.values_list("pk", flat=True))
            kept = set(role.permissions.exclude(pk__in=offered_perms).values_list("pk", flat=True))
            role.permissions.set(kept | {p.pk for p in self.cleaned_data["permissions"]})
            if not self.fields["members"].disabled:
                offered_people = set(self.fields["members"].queryset.values_list("pk", flat=True))
                hidden = set(role.user_set.exclude(pk__in=offered_people).values_list("pk", flat=True))
                role.user_set.set(hidden | {u.pk for u in self.cleaned_data["members"]})
            return role

    return RoleForm
