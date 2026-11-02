import csv
import io

from django.urls import reverse

from core.crud.access import Ctx
from core.reports.builder.definition import ReportError, validate_definition
from core.reports.builder.run import build_queryset
from core.reports.registry import get_report_source
from core.reports.resources.formats import available_formats

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user

PERMS = ["dummyapp.view_garment", "dummyapp.add_garment", "dummyapp.change_garment"]


class ReportTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.source = get_report_source("garments")
        self.user = make_user("r@example.com", self.company_a, perms=PERMS)
        self.ctx = Ctx(user=self.user, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk, member_ids=frozenset({self.company_a.pk}))
        for n, p, st in (("a", 10, "draft"), ("b", 20, "draft"), ("c", 30, "ready")):
            Garment.objects.create(company=self.company_a, name=n, style_no=n, price=p, status=st)
        Garment.objects.create(company=self.company_b, name="foreign", style_no="f", price=999, status="draft")

    def _rows(self, definition):
        spec = validate_definition(self.source, definition)
        return list(build_queryset(self.source, spec, self.ctx).queryset)

    def test_grouped_aggregates_respect_company_scope(self):
        rows = self._rows({"groupings": ["status"], "aggregates": [{"fn": "sum", "column": "price", "alias": "total"}, {"fn": "count", "column": "", "alias": "n"}]})
        by = {r["status"]: (r["total"], r["n"]) for r in rows}
        self.assertEqual(by, {"draft": (30, 2), "ready": (30, 1)})        # the foreign 999 never appears

    def test_calculated_column_and_safe_division(self):
        rows = self._rows({"groupings": ["status"], "aggregates": [{"fn": "sum", "column": "price", "alias": "total"}, {"fn": "count", "column": "", "alias": "n"}],
                           "calculated": [{"alias": "avg_price", "op": "div", "left": "total", "right": "n"}]})
        self.assertEqual({r["status"]: r["avg_price"] for r in rows}["draft"], 15)

    def test_allowlist_rejects_unknown_columns_functions_and_sort(self):
        for bad in ({"columns": ["secret_note"]}, {"groupings": ["price"]}, {"aggregates": [{"fn": "exec", "column": "price"}]},
                    {"aggregates": [{"fn": "sum", "column": "name"}]}, {"sorting": ["nope"]}, {"calculated": [{"alias": "x", "op": "pow", "left": 1, "right": 2}]},
                    {"aggregates": [{"fn": "count", "column": "", "alias": "name"}]}):
            with self.assertRaises(ReportError, msg=str(bad)):
                validate_definition(self.source, bad)

    def test_preview_view_and_permission(self):
        client = self.login(self.user)
        url = reverse("core:report_builder", args=["garments"])
        self.assertEqual(client.get(url).status_code, 200)
        nobody = make_user("n@example.com", self.company_a)
        self.assertEqual(self.login(nobody).get(url).status_code, 403)

    def test_csv_export_is_chunked_and_scoped(self):
        client = self.login(self.user)
        r = client.get(reverse("core:resource_export", args=["garments", "csv"]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(len(rows) - 1, 3)
        self.assertNotIn("foreign", body)

    def test_csv_formula_injection_is_neutralised(self):
        Garment.objects.create(company=self.company_a, name="=HYPERLINK(\"http://x\")", style_no="inj")
        body = self.login(self.user).get(reverse("core:resource_export", args=["garments", "csv"])).content.decode()
        self.assertNotIn(",=HYPERLINK", body)
        self.assertNotIn("\n=HYPERLINK", body)

    def test_all_declared_formats_available_or_cleanly_refused(self):
        client = self.login(self.user)
        for ext in ("csv", "tsv", "xlsx", "json", "yaml", "ods", "html", "pdf"):
            r = client.get(reverse("core:resource_export", args=["garments", ext]))
            self.assertIn(r.status_code, (200, 404), ext)
            if ext in available_formats() or ext == "pdf":
                self.assertEqual(r.status_code, 200, ext)

    def test_pdf_export_is_a_pdf(self):
        r = self.login(self.user).get(reverse("core:resource_export", args=["garments", "pdf"]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_import_is_dry_run_first_and_company_forced(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        client = self.login(self.user)
        csv_bytes = b"name,style_no,price,status\nImported,I1,5.00,draft\n"
        url = reverse("core:resource_import", args=["garments"])
        r = client.post(url, {"format": "csv", "file": SimpleUploadedFile("g.csv", csv_bytes, "text/csv")})
        self.assertEqual(r.status_code, 200, r.context and r.context.get("errors"))
        self.assertFalse(Garment.objects.filter(name="Imported").exists())                # preview wrote nothing
        r = client.post(url, {"token": client.session["core_import_token"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Garment.objects.get(name="Imported").company_id, self.company_a.pk)

    def test_import_rejects_bad_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        r = self.login(self.user).post(reverse("core:resource_import", args=["garments"]),
                                       {"format": "csv", "file": SimpleUploadedFile("evil.exe", b"MZ", "application/octet-stream")})
        self.assertNotEqual(r.status_code, 200)

    def test_large_export_becomes_background_job_with_expiry(self):
        from django.test import override_settings

        from core.models import ExportJob

        client = self.login(self.user)
        with override_settings(CORE_EXPORT_SYNC_MAX_ROWS=1), self.captureOnCommitCallbacks(execute=True):
            r = client.get(reverse("core:resource_export", args=["garments", "xlsx"]))
        self.assertEqual(r.status_code, 302)                                # sent to the Exports page
        job = ExportJob.objects.get(owner=self.user)                       # the worker (sync in tests) ran it after commit
        self.assertEqual(job.status, ExportJob.DONE, job.error if hasattr(job, "error") else "")
        self.assertIsNotNone(job.expires_at)
        self.assertGreater(job.expires_at, job.finished_at)


class ReportSharingTests(CoreTestCase):
    """Standard (superuser) reports, per-user/group sharing, and the Reports menu on list pages."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        self.admin = make_user("root@example.com", self.company_a, superuser=True)
        self.owner = make_user("owner@example.com", self.company_a, perms=PERMS)
        self.friend = make_user("friend@example.com", self.company_a, perms=PERMS)
        self.stranger = make_user("stranger@example.com", self.company_a, perms=PERMS)
        self.in_b = make_user("b@example.com", self.company_b, perms=PERMS)
        self.group = Group.objects.create(name="planners")
        Garment.objects.create(company=self.company_a, name="mine", style_no="m", price=1, status="draft")
        Garment.objects.create(company=self.company_b, name="theirs", style_no="t", price=2, status="draft")
        self.builder = reverse("core:report_builder", args=["garments"])
        self.definition = '{"columns": ["name", "price"]}'

    def _save(self, user, name, url=None, **extra):
        from core.models import ReportDefinition

        data = {"action": "save", "name": name, "definition": self.definition, **extra}
        r = self.login(user).post(url or self.builder, data)
        self.assertEqual(r.status_code, 302, r.content[:300])
        return ReportDefinition.objects.get(owner=user, name=name)

    def _visible(self, user):
        from core.permissions import user_company_ids
        from core.reports.service import visible_definitions

        return set(visible_definitions(user, user_company_ids(user)).values_list("name", flat=True))

    def test_share_with_selected_users_and_groups(self):
        self.stranger.groups.add(self.group)
        d = self._save(self.owner, "Picked", visibility="users", shared_users=[str(self.friend.pk), str(self.in_b.pk)])
        self.assertEqual(set(d.shared_users.all()), {self.friend})          # another company's user cannot be picked
        self.assertIn("Picked", self._visible(self.friend))
        self.assertNotIn("Picked", self._visible(self.stranger))
        self.assertNotIn("Picked", self._visible(self.in_b))
        self._save(self.owner, "Picked", url=f"{self.builder}?def={d.pk}", visibility="users", shared_groups=[str(self.group.pk)])
        self.assertIn("Picked", self._visible(self.stranger))               # via the group
        self.assertNotIn("Picked", self._visible(self.friend))              # user list replaced

    def test_private_and_public(self):
        self._save(self.owner, "Mine only", visibility="private")
        self._save(self.owner, "For all", visibility="public")
        self.assertEqual(self._visible(self.friend) & {"Mine only", "For all"}, {"For all"})
        self.assertIn("For all", self._visible(self.in_b))                   # reports are company independent

    def test_saved_report_follows_its_owner_into_every_company(self):
        both = make_user("both@example.com", self.company_a, perms=PERMS)
        both.profile.companies.add(self.company_b)
        d = self._save(both, "Everywhere", visibility="private")
        self.assertFalse(hasattr(d, "company_id"))
        both.profile.active_company = self.company_b
        both.profile.save()
        r = self.login(both).get(self.builder, {"def": d.pk})              # opens in company B and shows B's rows only
        self.assertEqual(r.status_code, 200)
        self.assertIn("theirs", r.content.decode())
        self.assertNotIn("mine", r.content.decode())
        self.assertContains(self.login(both).get(reverse("core:report_list")), "Everywhere")

    def test_standard_report_needs_superuser_and_is_offered_everywhere(self):
        mine = self._save(self.owner, "Try standard", visibility="private", is_system="1")
        self.assertFalse(mine.is_system)
        std = self._save(self.admin, "Std", visibility="private", is_system="1")
        self.assertTrue(std.is_system)
        self.assertIn("Std", self._visible(self.friend))
        self.assertIn("Std", self._visible(self.in_b))
        r = self.login(self.in_b).get(self.builder, {"def": std.pk})       # run in company B: company B rows only
        self.assertEqual(r.status_code, 200)
        self.assertIn("theirs", r.content.decode())
        self.assertNotIn("mine", r.content.decode())

    def test_only_superusers_change_standard_reports_others_save_a_copy(self):
        from core.models import ReportDefinition

        std = self._save(self.admin, "Std", is_system="1")
        client = self.login(self.friend)
        r = client.post(f"{self.builder}?def={std.pk}", {"action": "save", "name": "Std", "definition": '{"columns": ["name"]}'})
        self.assertEqual(r.status_code, 302)
        std.refresh_from_db()
        self.assertEqual(std.columns, ["name", "price"])                     # untouched
        self.assertTrue(ReportDefinition.objects.filter(owner=self.friend, name="Std", is_system=False).exists())
        self.assertEqual(client.post(reverse("core:report_definition_action", args=[std.pk, "delete"])).status_code, 403)
        self.assertIn("Save as my copy", client.get(self.builder, {"def": std.pk}).content.decode())
        self.assertEqual(self.login(self.admin).post(reverse("core:report_definition_action", args=[std.pk, "delete"])).status_code, 302)
        self.assertFalse(ReportDefinition.objects.filter(pk=std.pk).exists())

    def test_cloning_twice_gives_unique_private_copies(self):
        from core.models import ReportDefinition

        std = self._save(self.admin, "Std", is_system="1")
        client = self.login(self.friend)
        for _ in range(2):
            self.assertEqual(client.post(reverse("core:report_definition_action", args=[std.pk, "clone"])).status_code, 302)
        copies = ReportDefinition.objects.filter(owner=self.friend).order_by("pk")
        self.assertEqual([c.name for c in copies], ["Std (copy)", "Std (copy) 2"])
        self.assertTrue(all(c.visibility == "private" and not c.is_system for c in copies))

    def test_reports_page_splits_standard_and_mine(self):
        self._save(self.admin, "Std report", is_system="1")
        self._save(self.friend, "Friend report", visibility="public")
        body = self.login(self.owner).get(reverse("core:report_list")).content.decode()
        mine_at = body.index("My &amp; shared reports")
        self.assertLess(body.index("Std report"), mine_at)
        self.assertGreater(body.index("Friend report"), mine_at)

    def test_list_page_offers_reports_for_its_model(self):
        from core.reports.service import definitions_for_model

        self._save(self.admin, "Std report", is_system="1")
        self._save(self.owner, "Mine report", visibility="private")
        url = reverse("core:resource_list", args=["garments"])
        body = self.login(self.owner).get(url).content.decode()
        for text in ("Standard reports", "Std report", "Mine report", "New report"):
            self.assertIn(text, body)
        self.assertNotIn("Mine report", self.login(self.friend).get(url).content.decode())
        nobody = make_user("noview@example.com", self.company_a)
        self.assertEqual(definitions_for_model(nobody, {self.company_a.pk}, Garment), ([], [], []))


class AutoReportSourceTests(CoreTestCase):
    """Resources without a report source of their own get one, scoped exactly like the list page."""

    def test_fabrics_get_an_automatic_source_scoped_like_the_list(self):
        from core.reports.registry import report_sources_for_model

        from tests.dummyapp.models import Fabric

        sources = report_sources_for_model(Fabric)
        self.assertEqual([s.key for s in sources], ["r-fabrics"])
        self.assertEqual([s.key for s in report_sources_for_model(Garment)], ["garments"])   # an explicit source wins
        Fabric.objects.create(company=self.company_a, name="Cotton")
        Fabric.objects.create(company=self.company_b, name="Silk")
        user = make_user("f@example.com", self.company_a, perms=["dummyapp.view_fabric"])
        ctx = Ctx(user=user, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk, member_ids=frozenset({self.company_a.pk}))
        source = sources[0]
        rows = list(build_queryset(source, validate_definition(source, {"columns": ["name"]}), ctx).queryset)
        self.assertEqual([r["name"] for r in rows], ["Cotton"])
        client = self.login(user)
        self.assertIn("New report", client.get(reverse("core:resource_list", args=["fabrics"])).content.decode())
        self.assertEqual(client.get(reverse("core:report_builder", args=["r-fabrics"])).status_code, 200)
        nobody = make_user("nf@example.com", self.company_a)
        self.assertEqual(self.login(nobody).get(reverse("core:report_builder", args=["r-fabrics"])).status_code, 403)


class UsersReportBuilderTests(CoreTestCase):
    """The core-users source: the builder lists its columns (including profile columns) and Preview / Search keep the chosen columns."""

    def setUp(self):
        super().setUp()
        from .base import make_user

        self.admin = make_user("report-root@example.com", self.company_a, superuser=True)
        self.client = self.login(self.admin)
        self.url = reverse("core:report_builder", args=["core-users"])

    def test_builder_lists_columns_with_labels_including_profile_columns(self):
        page = self.client.get(self.url)
        for name, label in (("email", "Email address"), ("department", "Department"), ("designation", "Designation"), ("default_company", "Default company"),
                            ("official_id", "Official ID"), ("joining_date", "Joining date"), ("contact_no", "Contact no.")):
            self.assertContains(page, f'value="{name}"')
            self.assertContains(page, label)
        self.assertContains(page, "report-root@example.com")             # default preview already shows rows

    def test_htmx_preview_uses_the_chosen_columns_and_search(self):
        import json

        headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "report-preview"}
        r = self.client.get(self.url, {"definition": json.dumps({"columns": ["email", "department"]}), "q": "report-root"}, **headers)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "<html")
        self.assertContains(r, "Department")
        self.assertContains(r, "report-root@example.com")
        self.assertNotContains(r, "Joining date")
        none = self.client.get(self.url, {"definition": json.dumps({"columns": ["email"]}), "q": "no-such-person-xyz"}, **headers)
        self.assertNotContains(none, "report-root@example.com")

    def _photo(self):
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, "PNG")
        profile = self.admin.profile
        profile.user_img.save("p.png", ContentFile(buf.getvalue()), save=True)
        return profile

    def test_image_columns_are_offered_and_rendered(self):
        import json

        self._photo()
        self.assertContains(self.client.get(self.url), 'value="photo"')
        self.assertContains(self.client.get(self.url), 'value="signature"')
        headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "report-preview"}
        r = self.client.get(self.url, {"definition": json.dumps({"columns": ["email", "photo", "signature"]})}, **headers)
        self.assertContains(r, f'<img src="{reverse("core:user_file", args=[self.admin.pk, "user_img"])}"')
        self.assertNotContains(r, "users/img/")                                     # never the storage path
        self.assertEqual(self.client.get(reverse("core:user_file", args=[self.admin.pk, "user_img"])).status_code, 200)

    def test_exports_never_contain_storage_paths_and_pdf_embeds_the_image(self):
        import json

        self._photo()
        definition = json.dumps({"columns": ["email", "photo"]})
        csv_body = self.client.post(self.url, {"action": "export", "format": "csv", "definition": definition}).content.decode()
        self.assertIn("Yes", csv_body)
        self.assertNotIn("users/img/", csv_body)
        pdf = self.client.post(self.url, {"action": "export", "format": "pdf", "definition": definition})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
