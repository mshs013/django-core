"""Report company scope: the user's setting (Profile), per-run choice, saved-report default, Company column, exports, audit."""
import csv
import io
import json

from django.test import RequestFactory
from django.urls import reverse

from core.models import AuditLog, Department, Designation, ReportDefinition
from core.reports import scope
from core.reports.builder.definition import validate_definition
from core.reports.builder.run import build_queryset
from core.reports.registry import get_report_source

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_company, make_user
from .test_modal_org import MODAL, PASSWORD

PERMS = ["dummyapp.view_garment", "dummyapp.add_garment", "dummyapp.change_garment"]


def set_scope(user, enabled=True, mode="all", chosen=()):
    profile = user.profile
    profile.report_all_companies, profile.report_company_scope = enabled, mode
    profile.save()
    profile.report_companies.set(chosen)


class ReportCompanyScopeTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.company_c = make_company("C")
        self.user = make_user("scope@example.com", self.company_a, perms=PERMS)
        self.user.profile.companies.add(self.company_b)                       # member of A and B, not C
        for company, name in ((self.company_a, "alpha"), (self.company_b, "bravo"), (self.company_c, "charlie")):
            Garment.objects.create(company=company, name=name, style_no=name[:3], price=10)
        self.source = get_report_source("garments")

    def names(self, ctx, definition=None):
        spec = validate_definition(self.source, definition or {"columns": ["name"]})
        return sorted(r["name"] for r in build_queryset(self.source, spec, ctx).queryset)

    def ctx(self, mode=None, chosen=None):
        return scope.build_ctx(self.user, self.company_a.pk, mode, chosen)

    # ---------------------------------------------------------------------------------------------------- resolution
    def test_default_without_setting_is_the_active_company(self):
        self.assertEqual(scope.resolve_report_companies(self.user), frozenset({self.company_a.pk}))
        self.assertEqual(self.names(self.ctx()), ["alpha"])

    def test_checkbox_off_means_active_company_even_when_a_run_asks_for_more(self):
        set_scope(self.user, enabled=False, mode="all")
        self.assertEqual(scope.resolve_report_companies(self.user, "all"), frozenset({self.company_a.pk}))
        self.assertEqual(self.names(self.ctx("all")), ["alpha"])

    def test_all_covers_every_membership_but_never_a_foreign_company(self):
        set_scope(self.user, mode="all")
        self.assertEqual(scope.resolve_report_companies(self.user), frozenset({self.company_a.pk, self.company_b.pk}))
        self.assertEqual(self.names(self.ctx()), ["alpha", "bravo"])

    def test_chosen_drops_a_foreign_company_and_a_company_the_user_left(self):
        set_scope(self.user, mode="chosen", chosen=[self.company_b, self.company_c])
        self.assertEqual(scope.resolve_report_companies(self.user), frozenset({self.company_b.pk}))
        self.assertEqual(scope.resolve_report_companies(self.user, "chosen", [self.company_c.pk, "junk"]), frozenset())
        self.user.profile.companies.remove(self.company_b)
        from core.permissions import cache

        cache.bump()
        self.assertEqual(scope.resolve_report_companies(self.user), frozenset())          # fail closed: no rows
        self.assertEqual(self.names(self.ctx()), [])

    def test_superuser_all_sees_every_active_company(self):
        root = make_user("root-scope@example.com", self.company_a, superuser=True)
        set_scope(root, mode="all")
        self.company_c.is_active = False
        self.company_c.save()
        self.assertEqual(scope.resolve_report_companies(root), frozenset({self.company_a.pk, self.company_b.pk}))
        self.company_c.is_active = True
        self.company_c.save()
        self.assertEqual(scope.resolve_report_companies(root), frozenset({self.company_a.pk, self.company_b.pk, self.company_c.pk}))

    def test_run_choice_comes_from_the_query_string(self):
        set_scope(self.user, mode="active")
        request = RequestFactory().get("/", {"company_scope": "chosen", "companies": [self.company_b.pk, self.company_c.pk]})
        request.user = self.user
        ctx = scope.report_ctx(request)
        self.assertEqual((ctx.report_scope, ctx.company_ids, ctx.company_id), ("chosen", frozenset({self.company_b.pk}), self.company_a.pk))

    # ---------------------------------------------------------------------------------------------------- company column
    def test_company_column_only_with_more_than_one_company(self):
        spec = validate_definition(self.source, {"columns": ["name"]})
        single = build_queryset(self.source, spec, self.ctx())
        self.assertNotIn("core_company", dict(single.columns))
        set_scope(self.user, mode="all")
        multi = build_queryset(self.source, spec, self.ctx())
        self.assertEqual(multi.columns[0], ("core_company", "Company"))
        self.assertEqual({r["name"]: r["core_company"] for r in multi.queryset}, {"alpha": "A", "bravo": "B"})
        grouped = validate_definition(self.source, {"groupings": ["status"], "aggregates": [{"fn": "count", "column": "", "alias": "n"}]})
        rows = list(build_queryset(self.source, grouped, self.ctx()).queryset)
        self.assertEqual(sorted((r["core_company"], r["n"]) for r in rows), [("A", 1), ("B", 1)])

    def test_preview_shows_company_with_full_name_tooltip(self):
        set_scope(self.user, mode="all")
        client = self.login(self.user)
        r = client.get(reverse("core:report_builder", args=["garments"]))
        self.assertContains(r, 'name="company_scope"')
        self.assertContains(r, 'title="Company B"')
        self.assertNotContains(r, "charlie")

    def test_query_builder_follows_the_scope(self):
        set_scope(self.user, mode="all")
        url = reverse("core:query_builder", args=["dummyapp.garment"])
        definition = json.dumps({"columns": [{"path": "name"}]})
        r = self.login(self.user).post(url, {"definition": definition, "action": "preview"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "bravo")
        self.assertContains(r, ">Company<")
        self.assertNotContains(r, "charlie")
        r = self.login(self.user).post(url, {"definition": definition, "action": "preview", "company_scope": "active"}, HTTP_HX_REQUEST="true")
        self.assertNotContains(r, "bravo")
        self.assertNotContains(r, ">Company<")

    # ---------------------------------------------------------------------------------------------------- exports + audit
    def test_csv_export_follows_the_scope_and_is_audited(self):
        client = self.login(self.user)
        url = reverse("core:resource_export", args=["garments", "csv"])
        rows = list(csv.reader(io.StringIO(client.get(url, {"company_scope": "all"}).content.decode("utf-8-sig"))))
        self.assertEqual(len(rows) - 1, 1)                                   # setting off: active company only
        self.assertNotIn("Company", rows[0])
        set_scope(self.user, mode="active")
        rows = list(csv.reader(io.StringIO(client.get(url, {"company_scope": "all"}).content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][0], "Company")
        self.assertEqual(sorted(r[0] for r in rows[1:]), ["A", "B"])
        body = client.get(url, {"company_scope": "chosen", "companies": [self.company_c.pk]}).content.decode("utf-8-sig")
        self.assertNotIn("charlie", body)
        log = AuditLog.objects.filter(message__startswith="export garments.csv").order_by("pk")
        self.assertIn("companies: All my companies (A, B)", log[1].message)
        self.assertNotIn("alpha", " ".join(log.values_list("message", flat=True)))         # never row data

    def test_report_export_and_preview_audit_the_mode(self):
        set_scope(self.user, mode="all")
        client = self.login(self.user)
        url = reverse("core:report_builder", args=["garments"])
        r = client.post(url, {"definition": json.dumps({"columns": ["name"]}), "action": "export", "format": "csv", "company_scope": "all"})
        body = r.content.decode("utf-8-sig")
        self.assertIn("Company", body.splitlines()[0])
        self.assertIn("bravo", body)
        row = AuditLog.objects.filter(event="report_run", message__startswith="export garments.csv").latest("pk")
        self.assertIn("All my companies (A, B)", row.message)

    def test_saved_report_remembers_its_company_choice(self):
        set_scope(self.user, mode="active")
        client = self.login(self.user)
        url = reverse("core:report_builder", args=["garments"])
        client.post(url, {"definition": json.dumps({"columns": ["name"]}), "action": "save", "name": "Both", "visibility": "private",
                          "company_scope": "all", "keep_scope": "1"})
        saved = ReportDefinition.objects.get(name="Both")
        self.assertEqual(saved.company_scope, "all")
        r = client.get(url, {"def": saved.pk})
        self.assertContains(r, "bravo")
        r = client.get(url, {"def": saved.pk, "company_scope": "active"})                # the run can still narrow it
        self.assertNotContains(r, "bravo")


class UserFormReportScopeTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.company_c = make_company("C")
        self.dept = Department.objects.create(name="Ops")
        self.desig = Designation.objects.create(name="Manager")
        self.root = make_user("root-uf@example.com", self.company_a, superuser=True)

    def payload(self, **extra):
        return {"email": "made@example.com", "full_name": "Made", "password1": PASSWORD, "password2": PASSWORD,
                "default_company": self.company_a.pk, "companies": [self.company_a.pk, self.company_b.pk], "dept": self.dept.pk,
                "designation": self.desig.pk, "is_active": "on", **extra}

    def test_fields_are_on_the_form_and_saved(self):
        client = self.login(self.root)
        form = client.get(reverse("core:resource_new", args=["users"]), **MODAL).content.decode()
        self.assertIn('name="report_all_companies"', form)
        self.assertIn('x-data="showWhen"', form)
        r = client.post(reverse("core:resource_new", args=["users"]),
                        self.payload(report_all_companies="on", report_company_scope="chosen", report_companies=[self.company_b.pk]), **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:300])
        profile = type(self.root).objects.get(email="made@example.com").profile
        self.assertEqual((profile.report_all_companies, profile.report_company_scope), (True, "chosen"))
        self.assertEqual(list(profile.report_companies.values_list("pk", flat=True)), [self.company_b.pk])

    def test_chosen_companies_must_be_memberships_and_not_empty(self):
        client = self.login(self.root)
        url = reverse("core:resource_new", args=["users"])
        r = client.post(url, self.payload(report_all_companies="on", report_company_scope="chosen", report_companies=[self.company_c.pk]), **MODAL)
        self.assertContains(r, "Not one of this user", status_code=400)
        r = client.post(url, self.payload(report_all_companies="on", report_company_scope="chosen"), **MODAL)
        self.assertContains(r, "Choose at least one company", status_code=400)

    def test_unticked_box_saves_active_only(self):
        r = self.login(self.root).post(reverse("core:resource_new", args=["users"]), self.payload(report_company_scope="all"), **MODAL)
        self.assertEqual(r.status_code, 204)
        user = type(self.root).objects.get(email="made@example.com")
        self.assertFalse(user.profile.report_all_companies)
        self.assertEqual(scope.resolve_report_companies(user), frozenset({self.company_a.pk}))
