"""User create/edit form, in tabs:

* **Profile** -- account (email, name, active), a password on creation, department, designation, ids, photo, signature;
* **Companies** -- default company and memberships;
* **Roles & permissions** -- roles (auth groups) and single permissions, either or both;
* **Object access** -- for each access source (``core.permissions.access``, e.g. buyers): the rows this user works with, or all;
* **User access** -- the users whose records this user also sees on owner-scoped pages, or everyone's;
* **Approvals** -- the approval steps this user may decide, and the approval types on whose requests they may add steps;
* **Reports** -- "Report shows all companies" and, when ticked, which companies reports and exports cover by default
  (active / all of the user's companies / chosen ones; see ``core.reports.scope``).

Safety rules: an editor can only hand out what they hold themselves -- permissions they have, roles made of such permissions, rows
they may see, "all" only when they are not limited themselves (no privilege escalation through the user form); non-superusers
cannot edit superuser accounts and only superusers set the staff / superuser flags (staff create and edit normal users); nobody can deactivate their own account here; memberships, roles, permissions, rows and steps the
editor cannot see are preserved on save."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.urls import reverse

from core.crud.forms import ValidatedImageField, style_form
from core.crud.widgets import CheckboxList, permission_choices_field

PROFILE_FILES = ("user_img", "user_sign")
REPORT_TAB = ("report_all_companies", "report_company_scope", "report_companies")
PROFILE_TAB = ("email", "full_name", "password1", "password2", "dept", "designation", "official_id", "joining_date", "contact_no",
               "user_img", "user_sign", "is_active", "is_staff", "is_superuser")


def _source_key(model) -> str:
    return f"{model._meta.app_label}_{model._meta.model_name}"


def _audit_set(user, key: str, before: set, after: set, model) -> None:
    """One ``permission`` audit row on the user for an access list written with bulk_create / delete (no m2m signal): the old and
    new names; ``None`` = "all (no limit)". Nothing changed = no row."""
    from core.audit.service import record_event

    if before == after:
        return
    manager = getattr(model, "global_objects", None) or model._base_manager
    names = {pk: str(obj) for pk, obj in manager.in_bulk([i for i in before | after if i is not None]).items()}
    label = lambda ids: sorted(names.get(i, f"#{i} (deleted)") if i is not None else "All (no limit)" for i in ids)    # noqa: E731
    added, removed = label(after - before), label(before - after)
    message = "; ".join(p for p in (f"{key}: added {', '.join(added)}" if added else "", f"{key}: removed {', '.join(removed)}" if removed else "") if p)
    record_event("permission", obj=user, changes={key: [label(before), label(after)]}, message=message)


def user_form_class(cfg, ctx):
    from django.contrib.contenttypes.models import ContentType

    from core.models import ApprovalType, ApprovalTypeStep, Company, Department, Designation, ObjectAccess, UserAccess
    from core.permissions import invalidate, user_has_perm
    from core.permissions.access import access_sources, allowed_ids, restrict, visible_owner_ids
    from core.permissions.grants import grantable_permissions, grantable_roles

    User = get_user_model()
    actor = ctx.user
    everything = bool(getattr(actor, "is_superuser", False))

    def visible_companies():
        qs = Company.objects.filter(is_active=True)
        return qs if everything else qs.filter(pk__in=list(ctx.company_ids))

    def visible_people():
        people = User.objects.filter(is_active=True).order_by("full_name", "email")
        return people if everything else people.filter(profile__companies__in=ctx.member_ids).distinct()

    def person_label(u):
        return f"{u.full_name or u.email} · {u.email}"

    def _adder_rows():
        """Links of ``ApprovalType.step_adders`` (user may add steps on that type's requests)."""
        return ApprovalType.step_adders.through.objects.all()

    class UserForm(forms.ModelForm):
        password1 = forms.CharField(label="Password", strip=False, required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
        password2 = forms.CharField(label="Confirm password", strip=False, required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
        default_company = forms.ModelChoiceField(queryset=Company.objects.none(), label="Default company")
        companies = forms.ModelMultipleChoiceField(queryset=Company.objects.none(), required=False, label="Member of", widget=CheckboxList,
                                                   help_text="Companies this user can work in. The default company is always included.")
        dept = forms.ModelChoiceField(queryset=Department.objects.none(), label="Department")
        designation = forms.ModelChoiceField(queryset=Designation.objects.none(), label="Designation")
        official_id = forms.CharField(max_length=50, required=False, label="Official ID")
        joining_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
        contact_no = forms.CharField(max_length=50, required=False, label="Contact no.")
        user_img = ValidatedImageField(required=False, label="Photo")
        user_sign = ValidatedImageField(required=False, label="Signature")

        class Meta:
            model = User
            fields = ("email", "full_name", "is_active", "is_staff", "is_superuser")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            user = self.instance
            if user.pk and user.is_superuser and not everything:
                raise PermissionDenied("Only a superuser can edit a superuser account.")
            self.creating = user.pk is None
            if everything:                                    # only a superuser makes staff / superuser accounts
                self.fields["is_staff"].help_text = "Staff can manage normal users, roles and the audit log."
                self.fields["is_superuser"].help_text = "Superusers hold every permission and see every row."
            else:
                del self.fields["is_staff"], self.fields["is_superuser"]
            profile = user.profile if not self.creating else None
            companies = visible_companies()
            keep = lambda current: Q(pk=current) if current else Q(pk__in=[])    # noqa: E731  (an inactive / out-of-scope current value stays selectable)
            self.fields["default_company"].queryset = Company.objects.filter(Q(pk__in=companies.values("pk")) | keep(profile.default_company_id if profile else None))
            self.fields["companies"].queryset = companies
            self.fields["dept"].queryset = Department.objects.filter(Q(is_active=True) | keep(profile.dept_id if profile else None))
            self.fields["designation"].queryset = Designation.objects.filter(Q(is_active=True) | keep(profile.designation_id if profile else None))
            if self.creating:
                self.fields["password1"].required = self.fields["password2"].required = True
            else:
                del self.fields["password1"], self.fields["password2"]
                self.initial.update({"default_company": profile.default_company_id, "dept": profile.dept_id, "designation": profile.designation_id,
                                     "companies": list(profile.companies.filter(pk__in=companies.values("pk")).values_list("pk", flat=True)),
                                     "official_id": profile.official_id, "joining_date": profile.joining_date, "contact_no": profile.contact_no})
                for name in PROFILE_FILES:                        # the stored photo / signature shows above its upload input
                    if getattr(profile, name):
                        self.initial[name] = getattr(profile, name)
                        self.fields[name].widget.preview_url = reverse("core:user_file", args=[user.pk, name])
            self._roles_and_permissions()
            self._object_access()
            self._user_access()
            self._approvals()
            self._report_scope()
            self.tabs = (("Profile", PROFILE_TAB), ("Companies", ("default_company", "companies")),
                         ("Roles & permissions", ("groups", "user_permissions")),
                         ("Object access", tuple(n for n in self.fields if n.startswith("access_"))),
                         ("User access", ("see_users", "see_all_users")), ("Approvals", ("approver_steps", "step_adder_types")),
                         ("Reports", REPORT_TAB))
            style_form(self)

        # ---------------------------------------------------------------------------------------------- tab builders
        def _roles_and_permissions(self):
            user = self.instance
            self.fields["groups"] = forms.ModelMultipleChoiceField(
                queryset=grantable_roles(actor).order_by("name"), required=False, label="Roles", widget=CheckboxList,
                help_text="" if everything else "Only roles made of permissions you hold yourself are listed.")
            self.fields["user_permissions"] = permission_choices_field(
                grantable_permissions(actor), label="Single permissions",
                help_text="Given directly, in addition to the roles." + ("" if everything else " Only permissions you hold are listed."))
            if user.pk:
                self.initial["groups"] = list(user.groups.values_list("pk", flat=True))
                self.initial["user_permissions"] = list(user.user_permissions.values_list("pk", flat=True))

        def _object_access(self):
            self.access_fields = []
            user = self.instance
            for source in access_sources():
                key = _source_key(source)
                plural = str(source._meta.verbose_name_plural)
                ct = ContentType.objects.get_for_model(source)
                rows = source._default_manager.all()
                if not everything and getattr(source, "company_lookup", None) and hasattr(rows, "for_company_ids"):
                    rows = rows.for_company_ids(ctx.company_ids)
                rows = restrict(rows, actor)
                field = forms.ModelMultipleChoiceField(queryset=rows, required=False, label=plural.capitalize(), widget=CheckboxList,
                                                       help_text=f"This user works only with the {plural} ticked here. None ticked = none.")
                self.fields[f"access_{key}"] = field
                current = set(ObjectAccess.objects.filter(user_id=user.pk, content_type=ct).values_list("object_id", flat=True)) if user.pk else set()
                self.initial[f"access_{key}"] = [i for i in current if i is not None]
                if allowed_ids(actor, source) is None:                       # only an unlimited editor can grant "all"
                    self.fields[f"access_all_{key}"] = forms.BooleanField(required=False, label=f"All {plural} (no limit)")
                    self.initial[f"access_all_{key}"] = None in current
                self.access_fields.append((source, ct, key))

        def _user_access(self):
            user = self.instance
            people = visible_people().exclude(pk=user.pk) if user.pk else visible_people()
            self.fields["see_users"] = forms.ModelMultipleChoiceField(
                queryset=people, required=False, label="Also sees the records of", widget=CheckboxList,
                help_text="On pages limited to each user's own records, this user also sees the records of the users ticked here.")
            self.fields["see_users"].label_from_instance = person_label
            current = set(UserAccess.objects.filter(user_id=user.pk).values_list("target_id", flat=True)) if user.pk else set()
            self.initial["see_users"] = [i for i in current if i is not None]
            if visible_owner_ids(actor) is None:
                self.fields["see_all_users"] = forms.BooleanField(required=False, label="Everyone's records (no limit)")
                self.initial["see_all_users"] = None in current

        def _approvals(self):
            if not (everything or user_has_perm(actor, "core.change_approvaltype")):
                return
            steps = (ApprovalTypeStep.objects.select_related("approval_type", "step")
                     .order_by("approval_type__name", "position", "pk"))
            if not everything:
                steps = steps.filter(Q(approval_type__company__isnull=True) | Q(approval_type__company_id__in=ctx.member_ids))
            field = forms.ModelMultipleChoiceField(queryset=steps, required=False, label="Approves these steps", widget=CheckboxList,
                                                   help_text="This user may decide the ticked steps (in addition to roles and permissions).")
            field.label_from_instance = lambda s: f"{s.approval_type.name} › {s.step.name}" + (" (final)" if s.is_final else "")
            self.fields["approver_steps"] = field
            if self.instance.pk:
                self.initial["approver_steps"] = list(self.instance.approver_steps.values_list("pk", flat=True))
            types = ApprovalType.objects.order_by("name")
            if not everything:
                types = types.filter(Q(company__isnull=True) | Q(company_id__in=ctx.member_ids))
            self.fields["step_adder_types"] = forms.ModelMultipleChoiceField(
                queryset=types, required=False, label="May add steps on these approval types", widget=CheckboxList,
                help_text="On a pending request of a ticked type, this user may insert an extra step and choose its approvers.")
            if self.instance.pk:
                self.initial["step_adder_types"] = list(_adder_rows().filter(user_id=self.instance.pk).values_list("approvaltype_id", flat=True))

        def _report_scope(self):
            """Report company scope. The select and the company list are only shown when they apply (``show_when``, Alpine); the
            server validates them in ``clean`` whatever the browser sent."""
            from core.models.identity import REPORT_SCOPES

            profile = self.instance.profile if not self.creating else None
            self.fields["report_all_companies"] = forms.BooleanField(
                required=False, label="Report shows all companies",
                help_text="Off: reports, queries and exports use the active company only. On: choose the default below; each run can change it.")
            self.fields["report_company_scope"] = forms.ChoiceField(choices=REPORT_SCOPES, initial="active", required=False, label="Report companies",
                                                                    help_text="All my companies = every company this user belongs to (every company for a superuser).")
            self.fields["report_company_scope"].show_when = [["report_all_companies", ["on"]]]
            self.fields["report_companies"] = forms.ModelMultipleChoiceField(
                queryset=visible_companies(), required=False, label="Chosen companies", widget=CheckboxList,
                help_text="Only companies this user belongs to (any company for a superuser); a company they leave is dropped when a report runs.")
            self.fields["report_companies"].show_when = [["report_all_companies", ["on"]], ["report_company_scope", ["chosen"]]]
            if profile is not None:
                self.initial.update({"report_all_companies": profile.report_all_companies, "report_company_scope": profile.report_company_scope,
                                     "report_companies": list(profile.report_companies.filter(pk__in=visible_companies().values("pk"))
                                                              .values_list("pk", flat=True))})

        def _clean_report_scope(self, data):
            if not data.get("report_all_companies") or data.get("report_company_scope") != "chosen":
                return
            chosen = set(data.get("report_companies") or ())
            if not chosen:
                self.add_error("report_companies", "Choose at least one company, or pick another option.")
                return
            superuser = data.get("is_superuser") if "is_superuser" in self.fields else bool(self.instance.is_superuser)
            if superuser:
                return
            members = set(data.get("companies") or ()) | ({data["default_company"]} if data.get("default_company") else set())
            if not self.creating:                                 # memberships the editor cannot see stay (see save)
                members |= set(self.instance.profile.companies.exclude(pk__in=visible_companies().values("pk")))
            outside = sorted(str(c) for c in chosen - members)
            if outside:
                self.add_error("report_companies", f"Not one of this user's companies: {', '.join(outside)}.")

        # ---------------------------------------------------------------------------------------------- validation / save
        def clean_email(self):
            return (self.cleaned_data.get("email") or "").strip().lower()

        def clean(self):
            data = super().clean()
            if self.creating:
                p1, p2 = data.get("password1"), data.get("password2")
                if p1 and p2 and p1 != p2:
                    self.add_error("password2", "The two passwords do not match.")
                elif p1:
                    try:
                        validate_password(p1, User(email=data.get("email") or "", full_name=data.get("full_name") or ""))
                    except ValidationError as exc:
                        self.add_error("password1", exc)
            if not self.creating and self.instance.pk == actor.pk and data.get("is_active") is False:
                self.add_error("is_active", "You cannot deactivate your own account.")
            if not self.creating and self.instance.pk == actor.pk and "is_superuser" in self.fields and data.get("is_superuser") is False:
                self.add_error("is_superuser", "You cannot remove your own superuser status.")
            self._clean_report_scope(data)
            return data

        @staticmethod
        def _merged(current: set, offered: set, chosen: set) -> set:
            """The new set: what was chosen among the offered items, plus current items the editor was not offered."""
            return (current - offered) | chosen

        def save(self, commit=True):
            from core.services.org import ensure_profile

            user = super().save(commit=False)
            if self.creating:
                user.set_password(self.cleaned_data["password1"])
            user.save()
            profile = ensure_profile(user)
            d = self.cleaned_data
            profile.default_company, profile.dept, profile.designation = d["default_company"], d["dept"], d["designation"]
            profile.official_id, profile.joining_date, profile.contact_no = d.get("official_id", ""), d.get("joining_date"), d.get("contact_no", "")
            for name in PROFILE_FILES:
                value = d.get(name)
                if value is False:                                    # "clear" ticked
                    setattr(profile, name, None)
                elif value:
                    setattr(profile, name, value)
            if not profile.active_company_id:
                profile.active_company = d["default_company"]
            profile.report_all_companies = bool(d.get("report_all_companies"))
            profile.report_company_scope = d.get("report_company_scope") or "active"
            profile.save()
            chosen = set(d.get("companies") or []) | {d["default_company"]}
            hidden = set(profile.companies.exclude(pk__in=visible_companies().values("pk")))      # memberships the editor cannot see stay
            profile.companies.set(chosen | hidden)
            offered = set(visible_companies())
            current = set(profile.report_companies.all())
            picked = set(d.get("report_companies") or ()) if profile.report_company_scope == "chosen" else set()
            profile.report_companies.set(self._merged(current, offered, picked) if profile.report_company_scope == "chosen" else set())
            self._save_m2m_choice(user.groups, "groups")
            self._save_m2m_choice(user.user_permissions, "user_permissions")
            self._save_object_access(user)
            self._save_user_access(user)
            if "approver_steps" in self.fields:
                self._save_m2m_choice(user.approver_steps, "approver_steps")
            if "step_adder_types" in self.fields:                 # no reverse manager (related_name="+"): use the link table
                rows = _adder_rows().filter(user_id=user.pk)
                offered = set(self.fields["step_adder_types"].queryset.values_list("pk", flat=True))
                current = set(rows.values_list("approvaltype_id", flat=True))
                wanted = self._merged(current, offered, {t.pk for t in self.cleaned_data.get("step_adder_types") or ()})
                rows.exclude(approvaltype_id__in=wanted).delete()
                _adder_rows().bulk_create([_adder_rows().model(user_id=user.pk, approvaltype_id=pk) for pk in wanted - current])
                _audit_set(user, "step_adders", current, wanted, ApprovalType)
            invalidate()                                  # bulk writes above send no signals: drop cached access answers
            return user

        def _save_m2m_choice(self, manager, name):
            offered = set(self.fields[name].queryset.values_list("pk", flat=True))
            current = set(manager.values_list("pk", flat=True))
            manager.set(self._merged(current, offered, {o.pk for o in self.cleaned_data.get(name) or ()}))

        def _save_object_access(self, user):
            for source, ct, key in self.access_fields:
                rows = ObjectAccess.objects.filter(user=user, content_type=ct)
                before = set(rows.values_list("object_id", flat=True))
                offered = set(self.fields[f"access_{key}"].queryset.values_list("pk", flat=True))
                current = set(rows.exclude(object_id__isnull=True).values_list("object_id", flat=True))
                wanted = self._merged(current, offered, {o.pk for o in self.cleaned_data.get(f"access_{key}") or ()})
                rows.exclude(object_id__isnull=True).exclude(object_id__in=wanted).delete()
                ObjectAccess.objects.bulk_create([ObjectAccess(user=user, content_type=ct, object_id=pk) for pk in wanted - current])
                if f"access_all_{key}" in self.fields:
                    if self.cleaned_data.get(f"access_all_{key}"):
                        ObjectAccess.objects.get_or_create(user=user, content_type=ct, object_id=None)
                    else:
                        rows.filter(object_id__isnull=True).delete()
                _audit_set(user, f"object access: {source._meta.verbose_name_plural}", before,
                           set(rows.values_list("object_id", flat=True)), source)

        def _save_user_access(self, user):
            rows = UserAccess.objects.filter(user=user)
            before = set(rows.values_list("target_id", flat=True))
            offered = set(self.fields["see_users"].queryset.values_list("pk", flat=True))
            current = set(rows.exclude(target__isnull=True).values_list("target_id", flat=True))
            wanted = self._merged(current, offered, {u.pk for u in self.cleaned_data.get("see_users") or ()}) - {user.pk}
            rows.exclude(target__isnull=True).exclude(target_id__in=wanted).delete()
            UserAccess.objects.bulk_create([UserAccess(user=user, target_id=pk) for pk in wanted - current])
            if "see_all_users" in self.fields:
                if self.cleaned_data.get("see_all_users"):
                    UserAccess.objects.get_or_create(user=user, target=None)
                else:
                    rows.filter(target__isnull=True).delete()
            _audit_set(user, "see_users", before, set(rows.values_list("target_id", flat=True)), User)

    return UserForm
