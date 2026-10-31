from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils.text import slugify

from .base import AuditTrackedModel, CreatedInfoModel, UpdatedInfoModel
from .identity import REPORT_SCOPES

PRIVATE, USERS, PUBLIC = "private", "users", "public"
SAVED_REPORT_SCOPES = [("", "The user's own setting")] + REPORT_SCOPES
REPORT_VISIBILITY = [(PRIVATE, "Private (only me)"), (USERS, "Selected users / groups"), (PUBLIC, "All users (with access to the source)")]


class ReportDefinition(CreatedInfoModel, UpdatedInfoModel, AuditTrackedModel):
    """Saved report (company independent: it runs on the active company's rows): everything is JSON validated against the registered source allowlist (never raw SQL / arbitrary code)."""

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170)
    source_key = models.CharField(max_length=100, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_definitions")
    visibility = models.CharField(max_length=10, choices=REPORT_VISIBILITY, default=PRIVATE)
    shared_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="shared_reports",
                                          help_text="Who may run it when visibility is “Selected users / groups”.")
    shared_groups = models.ManyToManyField(Group, blank=True, related_name="shared_reports")
    is_system = models.BooleanField("standard report", default=False,
                                    help_text="Built by an administrator; offered to every user who can use the source, in every company.")
    description = models.CharField(max_length=300, blank=True)
    columns = models.JSONField(default=list, blank=True)
    filters = models.JSONField(default=dict, blank=True)
    saved_search = models.ForeignKey("core.SavedSearch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    groupings = models.JSONField(default=list, blank=True)
    aggregates = models.JSONField(default=list, blank=True)
    sorting = models.JSONField(default=list, blank=True)
    calculated = models.JSONField("calculated columns", default=list, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    page_size = models.PositiveSmallIntegerField(default=25)
    chart = models.JSONField(default=dict, blank=True, help_text='{"type": "bar|line|area|pie|doughnut", "x": column, "y": [columns]}')
    company_scope = models.CharField("companies", max_length=10, choices=SAVED_REPORT_SCOPES, default="", blank=True,
                                     help_text="Default company scope when it is run (blank = the user's own setting).")
    scope_companies = models.ManyToManyField("core.Company", blank=True, related_name="+", verbose_name="chosen companies")
    menu_parent = models.ForeignKey("core.MenuItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                    verbose_name="show in menu under", help_text="A menu group; empty = not in the sidebar menu.")
    menu_label = models.CharField(max_length=80, blank=True, help_text="Menu text (default: the report name).")
    menu_position = models.PositiveSmallIntegerField(default=100, help_text="Order among the group's entries (lower = higher up).")

    class Meta:
        verbose_name = "report definition"
        ordering = ("-is_system", "name", "pk")
        constraints = [models.UniqueConstraint(fields=["owner", "slug"], name="core_reportdef_unique_owner_slug")]

    def __str__(self) -> str:
        return self.name

    @property
    def menu_title(self) -> str:
        return self.menu_label or self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:170] or "report"
        super().save(*args, **kwargs)


class QueryReport(CreatedInfoModel, UpdatedInfoModel, AuditTrackedModel):
    """Saved query-builder report: the root model key plus a JSON definition that is re-validated against the query allowlist
    (``core.reports.query``) every time it is opened or run -- never raw SQL. Sharing works exactly like ``ReportDefinition``."""

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170)
    root = models.CharField("root model", max_length=100, db_index=True, help_text="app_label.model_name")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="query_reports")
    visibility = models.CharField(max_length=10, choices=REPORT_VISIBILITY, default=PRIVATE)
    shared_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="shared_query_reports")
    shared_groups = models.ManyToManyField(Group, blank=True, related_name="shared_query_reports")
    is_system = models.BooleanField("standard report", default=False,
                                    help_text="Built by an administrator; offered to every user who may use the root model, in every company.")
    description = models.CharField(max_length=300, blank=True)
    definition = models.JSONField(default=dict, blank=True)
    company_scope = models.CharField("companies", max_length=10, choices=SAVED_REPORT_SCOPES, default="", blank=True,
                                     help_text="Default company scope when it is run (blank = the user's own setting).")
    scope_companies = models.ManyToManyField("core.Company", blank=True, related_name="+", verbose_name="chosen companies")
    menu_parent = models.ForeignKey("core.MenuItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                    verbose_name="show in menu under", help_text="A menu group; empty = not in the sidebar menu.")
    menu_label = models.CharField(max_length=80, blank=True, help_text="Menu text (default: the report name).")
    menu_position = models.PositiveSmallIntegerField(default=100, help_text="Order among the group's entries (lower = higher up).")

    class Meta:
        verbose_name = "query report"
        ordering = ("-is_system", "name", "pk")
        constraints = [models.UniqueConstraint(fields=["owner", "slug"], name="core_queryreport_unique_owner_slug")]

    def __str__(self) -> str:
        return self.name

    @property
    def menu_title(self) -> str:
        return self.menu_label or self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:170] or "report"
        super().save(*args, **kwargs)
