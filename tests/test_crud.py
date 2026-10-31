from decimal import Decimal

from django.urls import reverse

from core.permissions import invalidate

from tests.dummyapp.models import Fabric, Garment, GarmentLine, LineOption

from .base import CoreTestCase, make_user

FULL = [f"dummyapp.{a}_{m}" for a in ("view", "add", "change", "delete") for m in ("garment", "garmentline", "lineoption", "fabric")]


class CrudTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("c@example.com", self.company_a, perms=FULL)
        self.client = self.login(self.user)
        self.url = lambda name, *a: reverse(f"core:resource_{name}", args=["garments", *a])

    def _payload(self, **extra):
        data = {"name": "Tee", "style_no": "T1", "price": "12.50", "status": "draft", "is_active": "on",
                "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-description": "Body", "lines-0-qty": "2", "lines-0-position": "0",
                "lines-0-options-TOTAL_FORMS": "1", "lines-0-options-INITIAL_FORMS": "0", "lines-0-options-MIN_NUM_FORMS": "0",
                "lines-0-options-MAX_NUM_FORMS": "1000", "lines-0-options-0-label": "Red", "lines-0-options-0-position": "0",
                "lines-1-description": "Sleeve", "lines-1-qty": "4", "lines-1-position": "1",
                "lines-1-options-TOTAL_FORMS": "0", "lines-1-options-INITIAL_FORMS": "0", "lines-1-options-MIN_NUM_FORMS": "0",
                "lines-1-options-MAX_NUM_FORMS": "1000"}
        data.update(extra)
        return data

    def test_create_with_nested_children_in_one_transaction(self):
        r = self.client.post(self.url("new"), self._payload())
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        g = Garment.objects.get(name="Tee")
        self.assertEqual(g.company_id, self.company_a.pk)                  # company is forced, never posted
        self.assertEqual(g.price, Decimal("12.50"))
        self.assertEqual([(l.description, l.position) for l in g.lines.order_by("position")], [("Body", 0), ("Sleeve", 1)])
        self.assertEqual(LineOption.objects.get().label, "Red")

    def test_invalid_child_rolls_back_the_parent(self):
        r = self.client.post(self.url("new"), self._payload(**{"lines-1-qty": "not-a-number"}))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Garment.objects.exists())
        self.assertFalse(GarmentLine.objects.exists())

    def test_posted_company_is_ignored(self):
        self.client.post(self.url("new"), self._payload(company=str(self.company_b.pk)))
        self.assertEqual(Garment.objects.get().company_id, self.company_a.pk)

    def test_other_company_object_is_404_not_403(self):
        other = Garment.objects.create(company=self.company_b, name="Secret", style_no="S")
        for name in ("detail", "edit", "delete"):
            self.assertEqual(self.client.get(self.url(name, other.pk)).status_code, 404, name)

    def test_object_in_scope_without_permission_is_403(self):
        mine = Garment.objects.create(company=self.company_a, name="Mine", style_no="M")
        weak = make_user("w@example.com", self.company_a, perms=["dummyapp.view_garment"])
        r = self.login(weak).get(self.url("edit", mine.pk))
        self.assertEqual(r.status_code, 403)

    def test_list_only_shows_active_company_rows_and_hides_deleted(self):
        Garment.objects.create(company=self.company_a, name="Visible", style_no="V")
        Garment.objects.create(company=self.company_b, name="Foreign", style_no="F")
        gone = Garment.objects.create(company=self.company_a, name="Trashed", style_no="X")
        gone.delete()
        body = self.client.get(self.url("list")).content.decode()
        self.assertIn("Visible", body)
        self.assertNotIn("Foreign", body)
        self.assertNotIn("Trashed", body)
        self.assertIn("Trashed", self.client.get(self.url("trash")).content.decode())

    def test_edit_reorders_and_deletes_children(self):
        self.client.post(self.url("new"), self._payload())
        g = Garment.objects.get()
        a, b = g.lines.order_by("position")
        data = self._payload(**{"lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "2", "lines-0-id": str(b.pk), "lines-0-garment": str(g.pk),
                                "lines-0-description": "Sleeve", "lines-0-qty": "4", "lines-0-position": "0",
                                "lines-1-id": str(a.pk), "lines-1-garment": str(g.pk), "lines-1-description": "Body", "lines-1-qty": "2",
                                "lines-1-position": "1", "lines-1-DELETE": "on", "lines-0-options-TOTAL_FORMS": "0",
                                "lines-1-options-TOTAL_FORMS": "0"})
        r = self.client.post(self.url("edit", g.pk), data)
        self.assertEqual(r.status_code, 302, r.content[:300])
        self.assertEqual(list(g.lines.values_list("description", flat=True)), ["Sleeve"])

    def test_inline_row_endpoint_allowlisted(self):
        ok = self.client.get(reverse("core:resource_inline_row", args=["garments", "lines"]) + "?index=3&prefix=lines")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("lines-3-description", ok.content.decode())
        for bad in ("nope", "lines.nope", "lines..options"):
            r = self.client.get(reverse("core:resource_inline_row", args=["garments", bad]) + "?index=0&prefix=lines")
            self.assertEqual(r.status_code, 404, bad)
        for raw in ("/r/garments/row/../x/", "/r/garments/row/..%2Fx/"):       # traversal never even routes
            self.assertEqual(self.client.get(raw + "?index=0&prefix=lines").status_code, 404, raw)

    def test_unknown_resource_is_404(self):
        self.assertEqual(self.client.get(reverse("core:resource_list", args=["auth-user"])).status_code, 404)

    def test_soft_delete_restore_via_ui(self):
        g = Garment.objects.create(company=self.company_a, name="Del", style_no="D")
        self.assertEqual(self.client.post(self.url("delete", g.pk)).status_code, 302)
        self.assertTrue(Garment.deleted_objects.filter(pk=g.pk).exists())
        self.assertEqual(self.client.post(self.url("restore", g.pk)).status_code, 302)
        self.assertTrue(Garment.objects.filter(pk=g.pk).exists())

    def test_csrf_enforced(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.login(email=self.user.email, password=self.password)
        self.assertEqual(strict.post(self.url("new"), self._payload()).status_code, 403)

    def test_field_type_coverage(self):
        from django import forms

        from core.crud.forms import formfield_for

        from django.db import models

        samples = {models.CharField(max_length=5): forms.CharField, models.TextField(): forms.CharField,
                   models.IntegerField(): forms.IntegerField, models.DecimalField(max_digits=5, decimal_places=2): forms.DecimalField,
                   models.BooleanField(): forms.BooleanField, models.DateField(): forms.DateField, models.DateTimeField(): forms.DateTimeField,
                   models.TimeField(): forms.TimeField, models.EmailField(): forms.EmailField, models.URLField(): forms.URLField,
                   models.UUIDField(): forms.UUIDField, models.JSONField(): forms.JSONField, models.FloatField(): forms.FloatField,
                   models.SlugField(): forms.SlugField, models.GenericIPAddressField(): forms.GenericIPAddressField,
                   models.DurationField(): forms.DurationField}
        for field, expected in samples.items():
            field.name = "f"
            self.assertIsInstance(formfield_for(field), expected, type(field).__name__)
