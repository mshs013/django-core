import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Department

from .base import CoreTestCase, make_user


class ImportPageTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_user("imp-root@example.com", self.company_a, superuser=True)

    def test_master_data_pages_offer_import_and_sensitive_ones_do_not(self):
        client = self.login(self.root)
        for slug in ("companies", "departments", "designations"):
            self.assertContains(client.get(reverse("core:resource_list", args=[slug])), reverse("core:resource_import", args=[slug]))
        for slug in ("users", "roles"):
            self.assertNotContains(client.get(reverse("core:resource_list", args=[slug])), f"/r/{slug}/import/")
            self.assertEqual(client.get(reverse("core:resource_import", args=[slug])).status_code, 404)
            self.assertEqual(client.get(reverse("core:resource_import_template", args=[slug, "csv"])).status_code, 404)

    def test_template_has_the_import_columns_and_round_trips(self):
        client = self.login(self.root)
        self.assertContains(client.get(reverse("core:resource_import", args=["departments"])), "Template (xlsx)")
        r = client.get(reverse("core:resource_import_template", args=["departments", "csv"]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        header = next(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertIn("id", header)
        self.assertIn("name", header)
        for system in ("company", "created_by", "created_at", "updated_by", "updated_at"):
            self.assertNotIn(system, header)                    # forced / stamped by the system, never read from the file
        xlsx = client.get(reverse("core:resource_import_template", args=["departments", "xlsx"]))
        self.assertEqual((xlsx.status_code, xlsx.content[:2]), (200, b"PK"))
        self.assertEqual(client.get(reverse("core:resource_import_template", args=["departments", "exe"])).status_code, 404)

        rows = io.StringIO()
        writer = csv.DictWriter(rows, fieldnames=header)
        writer.writeheader()
        writer.writerow({h: "" for h in header} | {"name": "Knitting floor", "is_active": "1"})
        url = reverse("core:resource_import", args=["departments"])
        r = client.post(url, {"format": "csv", "file": SimpleUploadedFile("d.csv", rows.getvalue().encode(), "text/csv")})
        self.assertEqual(r.status_code, 200, r.context and r.context.get("errors"))
        self.assertEqual(client.post(url, {"token": client.session["core_import_token"]}).status_code, 302)
        self.assertTrue(Department.objects.filter(name="Knitting floor").exists())

    def test_template_needs_add_permission(self):
        viewer = make_user("imp-view@example.com", self.company_a, perms=["core.view_department"])
        r = self.login(viewer).get(reverse("core:resource_import_template", args=["departments", "csv"]))
        self.assertEqual(r.status_code, 403)
