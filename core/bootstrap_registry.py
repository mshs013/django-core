"""Registers ``core``'s own generic resources and report sources (users, roles, companies, departments, designations, approvals,
notifications, audit, jobs, saved searches, saved reports). Business apps do the same for their models from ``AppConfig.ready``.

Every ``register_resource`` call gives a model its list / detail / form / delete pages, search and an automatic report source.
Most entries use ``menu=False`` because core's menu is stored in the database (``core/menu/defaults.py``), not built from here.
"""
from __future__ import annotations

# ``ready()`` can run more than once (tests, autoreload); registering twice would raise, so remember that we already ran.
_DONE = False


def register_core_registrations() -> None:
    global _DONE
    if _DONE:
        return
    _DONE = True

    # Imports live inside the function: models are only importable once the app registry is ready.
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    from core.crud.registry import Inline, register_resource
    from core.models import (
        ApprovalRequest, ApprovalStep, ApprovalType, ApprovalTypeStep, ArchiveJob, AuditLog, BackupRecord, Company, Department,
        Designation, ExportJob, Notification, NotificationRule, ReportDefinition, Role, SavedSearch,
    )
    from core.permissions import STAFF_PERM
    from core.reports.registry import ReportColumn, register_report_source

    User = get_user_model()
    if User._meta.label != "core.User":
        return  # a project with its own user model registers its own user resource (and the rest of this file assumes ours)

    from core.crud.approvalform import approval_type_form_class, notification_rule_form_class, validate_step_conditions
    from core.crud.companyform import company_form_class
    from core.crud.roleform import role_form_class
    from core.crud.service import CrudError
    from core.crud.userform import user_form_class

    # ------------------------------------------------------------------------------------------------------------------
    # Hooks: small callbacks the CRUD engine calls at fixed points (extra detail links, refusing a delete, ...).
    # A ``delete_guard`` raises CrudError with a message shown to the user instead of deleting.
    # ------------------------------------------------------------------------------------------------------------------
    def user_files(obj, request):
        """Detail page links to the user's photo/signature (private files, served through ``core:user_file``)."""
        from django.urls import reverse

        profile = getattr(obj, "profile", None)
        return [
            (label, "", reverse("core:user_file", args=[obj.pk, name]), True)
            for label, name in (("Photo", "user_img"), ("Signature", "user_sign"))
            if profile is not None and getattr(profile, name)
        ]

    def user_guard(obj, ctx):
        if obj.pk == ctx.user.pk:
            raise CrudError("You cannot delete your own account.")
        if (obj.is_superuser or obj.is_staff) and not ctx.user.is_superuser:
            raise CrudError("Only a superuser can delete a staff or superuser account.")

    def role_guard(obj, ctx):
        if not ctx.user.is_superuser:
            raise CrudError("Only a superuser can delete a role.")

    def company_guard(obj, ctx):
        """A parent company cannot be deleted while it still has children (they would lose their parent)."""
        kids = list(type(obj).objects.filter(parent_id=obj.pk).order_by("name").values_list("name", flat=True))
        if kids:
            raise CrudError(
                f"“{obj.name}” has {len(kids)} child {'company' if len(kids) == 1 else 'companies'} "
                f"({', '.join(kids[:5])}{'…' if len(kids) > 5 else ''}). Delete or move them to another parent first."
            )

    def owner_only(field):
        """``get_queryset`` hook: personal records (notifications, exports, saved searches...) show only the owner's rows."""
        return lambda qs, ctx: qs.filter(**{field: ctx.user})

    # ------------------------------------------------------------------------------------------------------------------
    # Users, roles, permissions
    # ------------------------------------------------------------------------------------------------------------------
    # Users: the custom ``user_form_class`` adds the tabs (profile, companies, roles, access, approvals). ``company_field``
    # scopes the list to users of the active company; ``select_related`` avoids one query per row for the profile columns.
    register_resource(
        User, slug="users", label="User", label_plural="Users", icon="users",
        list_fields=("photo", "email", "full_name", "profile__dept__name", "profile__designation__name",
                     "profile__default_company__abbreviation", "is_active"),
        detail_fields=("email", "full_name", "department_name", "designation_name", "default_company_name", "official_id",
                       "joining_date", "contact_no", "is_active", "is_staff", "is_superuser", "date_joined",
                       "created_by", "created_at", "updated_by", "updated_at"),
        form_fields=("email", "full_name", "is_active"),
        form_class=user_form_class,
        searchable_fields=("email", "full_name"),
        filterable_fields=("is_active", "is_staff", "date_joined"),
        sortable_fields=("email", "full_name", "date_joined", "profile__dept__name", "profile__designation__name",
                         "profile__default_company__abbreviation"),
        default_sort=("email",),
        labels={"profile__dept__name": "Department", "profile__designation__name": "Designation",
                "profile__default_company__abbreviation": "Company"},
        company_field="profile__companies",
        menu=False,
        hooks={
            "get_queryset": lambda qs, ctx: qs.select_related("profile__dept", "profile__designation", "profile__default_company"),
            "detail_extra": user_files,
            "delete_guard": user_guard,
        },
    )

    # Roles (auth groups): staff may view, add and change roles, handing out only permissions they hold themselves (see
    # ``permissions/grants.py``); only superusers delete. STAFF_PERM is a pseudo-permission meaning "user has the Staff flag".
    register_resource(
        Role, slug="roles", label="Role", label_plural="Roles", icon="shield-lock",
        list_fields=("name", "permission_count", "member_count"),
        detail_fields=("name", "permission_count", "member_count"),
        form_fields=("name",),
        form_class=role_form_class,
        searchable_fields=("name",),
        sortable_fields=("name",),
        default_sort=("name",),
        company_field=None,  # roles are global, not per company
        menu=False,
        permissions={"view": STAFF_PERM, "add": STAFF_PERM, "change": STAFF_PERM, "delete": "core.delete_role"},
        hooks={"delete_guard": role_guard},
    )

    # Permissions come from the code (model Meta), so the page is read-only: add/change/delete are disabled.
    register_resource(
        Permission, slug="permissions", label="Permission", label_plural="Permissions", icon="key",
        list_fields=("name", "codename", "content_type"),
        detail_fields=("name", "codename", "content_type"),
        searchable_fields=("name", "codename"),
        filterable_fields=("content_type",),
        sortable_fields=("name", "codename"),
        default_sort=("codename",),
        company_field=None,
        menu=False,
        allow_export=True,
        disabled_actions=("add", "change", "delete"),
        permissions={"view": STAFF_PERM},
        hooks={"get_queryset": lambda qs, ctx: qs.select_related("content_type")},
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Organisation (master data). Import is per page (``allow_import``): on for master data only. Users / roles /
    # permissions keep it off on purpose, because an import there could hand out rights.
    # ------------------------------------------------------------------------------------------------------------------
    # Companies: ``company_field="id"`` means a user sees only the companies they belong to; superusers see all of them.
    register_resource(
        Company, slug="companies", label="Company", label_plural="Companies", icon="building",
        list_fields=("logo", "name", "abbreviation", "parent", "is_parent", "is_active"),
        form_fields=("name", "abbreviation", "is_parent", "parent", "legal_name", "tax_id", "address", "factory_address",
                     "email", "phone", "fax", "website", "logo", "is_active"),
        form_class=company_form_class,
        searchable_fields=("name", "abbreviation", "email"),
        filterable_fields=("is_active", "is_parent", "parent"),
        sortable_fields=("name", "abbreviation"),
        default_sort=("name",),
        company_field="id",
        superuser_unscoped=True,
        menu=False,
        allow_import=True,
        hooks={"delete_guard": company_guard},
    )

    register_resource(
        Department, slug="departments", label="Department", label_plural="Departments", icon="sitemap",
        list_fields=("name", "parent", "is_active"),
        form_fields=("name", "parent", "is_active"),
        searchable_fields=("name",),
        filterable_fields=("parent", "is_active"),
        sortable_fields=("name",),
        default_sort=("name",),
        menu=False,
        allow_import=True,
    )

    register_resource(
        Designation, slug="designations", label="Designation", label_plural="Designations", icon="id-badge",
        list_fields=("name", "is_active"),
        form_fields=("name", "is_active"),
        searchable_fields=("name",),
        filterable_fields=("is_active",),
        sortable_fields=("name",),
        default_sort=("name",),
        menu=False,
        allow_import=True,
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Approvals. ``code`` is not shown: it is generated from the name and only links code-registered chains to their rows.
    # ------------------------------------------------------------------------------------------------------------------
    # Approval steps: reusable building blocks. The labels decide what approvers see in "Awaiting my approval"
    # (pass/fail radio labels, button text, optional Observation choice).
    register_resource(
        ApprovalStep, slug="approval-steps", label="Approval step", label_plural="Approval steps", icon="list-check",
        list_fields=("name", "display_name", "pass_label", "fail_label", "button_text", "sequence", "allow_observation",
                     "is_active"),
        form_fields=("name", "display_name", "signature_label", "pass_label", "fail_label", "button_text", "sequence",
                     "allow_observation", "permission", "skip_rule", "is_active"),
        searchable_fields=("name", "display_name"),
        filterable_fields=("is_active", "allow_observation"),
        sortable_fields=("sequence", "name"),
        default_sort=("sequence", "name"),
        company_field=None,
        menu=False,
    )

    # Approval types: a chain of steps for one model. The custom form limits "Applies to" to approvable models and adds an
    # Approvers tab; ``after_save`` re-checks step conditions once both the model and the steps are saved. The steps are a
    # nested inline (``position_field`` keeps them in order when rows are added or moved).
    register_resource(
        ApprovalType, slug="approval-types", label="Approval type", label_plural="Approval types", icon="route",
        list_fields=("name", "content_type", "reject_mode", "company", "is_active"),
        form_fields=("name", "content_type", "queue_fields", "reference_field", "reject_mode", "company", "permission",
                     "notify_on", "notify_users", "notify_groups", "notify_email", "export_buttons", "export_when", "is_active"),
        form_class=approval_type_form_class,
        searchable_fields=("name",),
        filterable_fields=("is_active", "reject_mode"),
        sortable_fields=("name",),
        default_sort=("name",),
        company_field=None,
        menu=False,
        hooks={"after_save": validate_step_conditions},
        inlines=(
            Inline(ApprovalTypeStep, slug="steps", label="Step", position_field="position",
                   fields=("step", "position", "is_final", "is_enabled", "permission", "approver_group", "condition")),
        ),
    )

    # Approval requests are created by the engine (submit / decide), never typed in, so the page is read-only.
    register_resource(
        ApprovalRequest, slug="approval-requests", label="Approval request", label_plural="Approval requests", icon="checklist",
        list_fields=("object_repr", "approval_type", "status", "submitted_by", "created_at"),
        detail_fields=("request_page", "object_repr", "approval_type", "content_type", "status", "steps_summary", "round",
                       "reject_mode", "submitted_by", "created_at", "completed_at"),
        labels={"content_type": "Model"},
        searchable_fields=("object_repr",),
        filterable_fields=("status", "approval_type", "created_at"),
        sortable_fields=("created_at", "status"),
        default_sort=("-created_at",),
        company_field="company",
        disabled_actions=("add", "change", "delete"),
        menu=False,
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Notifications and audit
    # ------------------------------------------------------------------------------------------------------------------
    register_resource(
        NotificationRule, slug="notification-rules", label="Notification rule", label_plural="Notification rules",
        icon="bell-cog",
        list_fields=("name", "content_type", "event", "company", "is_active"),
        form_fields=("name", "content_type", "event", "company", "watch_fields", "condition",
                     "notify_users", "notify_groups", "notify_creator", "notify_submitter", "notify_approvers", "user_field",
                     "notify_actor", "title", "body", "level", "send_in_app", "send_mail", "send_email", "is_active"),
        form_class=notification_rule_form_class,
        searchable_fields=("name",),
        filterable_fields=("is_active", "event", "content_type"),
        sortable_fields=("name",),
        default_sort=("name",),
        company_field=None,
        menu=False,
        allow_export=False,
    )

    # Audit log: written by signals only (read-only page). Staff see it; superusers see every company's events.
    register_resource(
        AuditLog, slug="audit", label="Audit event", label_plural="Audit log", icon="history",
        list_fields=("created_at", "event", "user_repr", "model_name", "object_repr", "message"),
        detail_fields=("created_at", "event", "user", "user_repr", "model_name", "object_pk", "object_repr", "changes_display",
                       "message", "ip_address"),
        searchable_fields=("user_repr", "model_label", "object_repr", "message"),
        filterable_fields=("event", "created_at", "model_label", "user"),
        sortable_fields=("created_at", "event", "user_repr"),
        default_sort=("-created_at",),
        company_field="company",
        superuser_unscoped=True,
        permissions={"view": STAFF_PERM},
        disabled_actions=("add", "change", "delete"),
        allow_import=False,
    )

    # Personal inbox: everyone may open it (``view: None``) but sees only their own notifications.
    register_resource(
        Notification, slug="notifications", label="Notification", label_plural="Notifications", icon="bell",
        list_fields=("created_at", "title", "level", "is_read"),
        searchable_fields=("title", "body"),
        filterable_fields=("is_read", "level", "created_at"),
        sortable_fields=("created_at",),
        default_sort=("-created_at",),
        company_field=None,
        permissions={"view": None},
        hooks={"get_queryset": owner_only("recipient")},
        disabled_actions=("add", "change", "delete"),
        menu=False,
        allow_export=False,
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Background jobs (read-only history; the jobs themselves are started from their own pages)
    # ------------------------------------------------------------------------------------------------------------------
    register_resource(
        ExportJob, slug="export-jobs", label="Export job", label_plural="Export jobs", icon="file-export",
        list_fields=("created_at", "target_key", "export_format", "status"),
        searchable_fields=("target_key",),
        filterable_fields=("status", "created_at"),
        sortable_fields=("created_at", "status"),
        default_sort=("-created_at",),
        company_field=None,
        permissions={"view": None},
        hooks={"get_queryset": owner_only("owner")},
        disabled_actions=("add", "change", "delete"),
        menu=False,
        allow_export=False,
    )

    register_resource(
        BackupRecord, slug="backups", label="Backup", label_plural="Backups", icon="database-export",
        list_fields=("created_at", "kind", "mode", "status", "file_size"),
        searchable_fields=("file_name", "message"),
        filterable_fields=("kind", "mode", "status", "created_at"),
        sortable_fields=("created_at", "status"),
        default_sort=("-created_at",),
        company_field=None,
        disabled_actions=("add", "change", "delete"),
        permissions={"view": "core.view_backuprecord"},
        menu=False,
        allow_export=False,
    )

    register_resource(
        ArchiveJob, slug="archive-jobs", label="Archive job", label_plural="Archive jobs", icon="archive",
        list_fields=("created_at", "kind", "table", "status", "processed_rows"),
        searchable_fields=("table",),
        filterable_fields=("kind", "status", "created_at"),
        sortable_fields=("created_at", "status"),
        default_sort=("-created_at",),
        company_field=None,
        disabled_actions=("add", "change", "delete"),
        permissions={"view": "core.view_archivejob"},
        menu=False,
        allow_export=False,
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Personal saved items: created from the search bar / report builder, so only delete is offered here.
    # ------------------------------------------------------------------------------------------------------------------
    register_resource(
        SavedSearch, slug="saved-searches", label="Saved search", label_plural="Saved searches", icon="bookmark",
        list_fields=("name", "target_key", "visibility", "is_default", "pinned_to_menu"),
        searchable_fields=("name", "target_key"),
        filterable_fields=("visibility", "is_default", "pinned_to_menu", "target_key"),
        sortable_fields=("name",),
        default_sort=("name",),
        company_field="company",
        permissions={"view": None, "delete": None},
        hooks={"get_queryset": owner_only("owner")},
        disabled_actions=("add", "change"),
        menu=False,
        allow_export=False,
    )

    register_resource(
        ReportDefinition, slug="report-definitions", label="Report", label_plural="Saved reports", icon="report",
        list_fields=("name", "source_key", "visibility", "created_at"),
        searchable_fields=("name", "source_key"),
        filterable_fields=("visibility", "source_key"),
        sortable_fields=("name", "created_at"),
        default_sort=("name",),
        company_field=None,  # saved reports work in every company
        permissions={"view": None, "delete": None},
        hooks={"get_queryset": owner_only("owner")},
        disabled_actions=("add", "change"),
        menu=False,
        allow_export=False,
    )

    # ------------------------------------------------------------------------------------------------------------------
    # Report sources for the report builder. A column is a field name, or
    # ReportColumn(key, label, lookup path, kind, groupable, aggregates, file url) for related or computed values.
    # ``company_lookup`` scopes rows to the active company; ``queryset`` narrows personal sources to the current user.
    # ------------------------------------------------------------------------------------------------------------------
    col = ReportColumn

    register_report_source(
        "core-users", User, label="Users",
        columns=(
            "email", "full_name", "is_active", "date_joined",
            col("department", "Department", "profile__dept__name", "text", True),
            col("designation", "Designation", "profile__designation__name", "text", True),
            col("default_company", "Default company", "profile__default_company__name", "text", True),
            col("official_id", "Official ID", "profile__official_id", "text", False),
            col("joining_date", "Joining date", "profile__joining_date", "date", True, ("count", "min", "max")),
            col("contact_no", "Contact no.", "profile__contact_no", "text", False),
            col("photo", "Photo", "profile__user_img", "image", False, (), ("core:user_file", "user_img")),
            col("signature", "Signature", "profile__user_sign", "image", False, (), ("core:user_file", "user_sign")),
        ),
        default_columns=("photo", "email", "full_name", "department", "designation", "default_company", "is_active"),
        filterable_fields=("is_active", "date_joined", "profile__dept__name", "profile__designation__name",
                           "profile__default_company__name", "profile__joining_date"),
        searchable_fields=("email", "full_name", "profile__official_id", "profile__contact_no", "profile__dept__name",
                           "profile__designation__name"),
        sortable_fields=("email", "date_joined"),
        default_sort=("email",),
        company_lookup="profile__companies",
        permission="core.view_user",
        date_field="date_joined",
    )

    register_report_source(
        "core-companies", Company, label="Companies",
        columns=("name", "abbreviation", "is_active"),
        filterable_fields=("is_active",),
        searchable_fields=("name", "abbreviation"),
        sortable_fields=("name",),
        default_sort=("name",),
        company_lookup="id",
        permission="core.view_company",
    )

    register_report_source(
        "core-departments", Department, label="Departments",
        columns=("name", col("parent_name", "Parent", "parent__name", "text", True)),
        filterable_fields=("parent",),
        searchable_fields=("name",),
        sortable_fields=("name",),
        default_sort=("name",),
        permission="core.view_department",
    )

    register_report_source(
        "core-designations", Designation, label="Designations",
        columns=("name",),
        searchable_fields=("name",),
        sortable_fields=("name",),
        default_sort=("name",),
        permission="core.view_designation",
    )

    register_report_source(
        "core-audit", AuditLog, label="Audit log",
        columns=(
            col("created_at", "When", "created_at", "datetime", False, ("count", "min", "max")),
            col("event", "Event", "event", "choice", True, ("count",)),
            "user_repr", "model_label", "object_repr", "message",
        ),
        filterable_fields=("event", "created_at", "model_label"),
        searchable_fields=("user_repr", "model_label", "message"),
        sortable_fields=("created_at", "event"),
        default_sort=("-created_at",),
        company_lookup="company",
        permission=STAFF_PERM,
        date_field="created_at",
    )

    register_report_source(
        "core-approval-requests", ApprovalRequest, label="Approval requests",
        columns=("object_repr", col("status", "Status", "status", "choice", True, ("count",)), "created_at", "round"),
        filterable_fields=("status", "created_at"),
        searchable_fields=("object_repr",),
        sortable_fields=("created_at",),
        default_sort=("-created_at",),
        company_lookup="company",
        permission="core.view_approvalrequest",
        date_field="created_at",
    )

    # Personal sources: no permission needed (``permission=()``), the queryset already limits rows to the current user.
    register_report_source(
        "core-export-jobs", ExportJob, label="Export jobs",
        columns=("target_key", "export_format", "status", "created_at"),
        filterable_fields=("status", "created_at"),
        sortable_fields=("created_at",),
        default_sort=("-created_at",),
        queryset=lambda ctx: ExportJob.objects.filter(owner=ctx.user),
        company_lookup=None,
        permission=(),
    )

    register_report_source(
        "core-saved-searches", SavedSearch, label="Saved searches",
        columns=("name", "target_key", "visibility"),
        searchable_fields=("name",),
        filterable_fields=("visibility", "target_key"),
        sortable_fields=("name",),
        default_sort=("name",),
        queryset=lambda ctx: SavedSearch.objects.filter(owner=ctx.user),
        company_lookup=None,
        permission=(),
    )

    register_report_source(
        "core-notifications", Notification, label="Notifications",
        columns=("title", "level", "is_read", "created_at"),
        searchable_fields=("title",),
        filterable_fields=("level", "is_read", "created_at"),
        sortable_fields=("created_at",),
        default_sort=("-created_at",),
        queryset=lambda ctx: Notification.objects.filter(recipient=ctx.user),
        company_lookup=None,
        permission=(),
    )
