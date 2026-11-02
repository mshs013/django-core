from core.audit.redaction import MASK
from core.models import AuditLog, User
from core.services import context

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user


class AuditTests(CoreTestCase):
    def test_create_update_delete_restore_are_recorded_with_old_new(self):
        user = make_user("a@example.com", self.company_a)
        with context.acting_as(user, "10.0.0.1", {self.company_a.pk}, self.company_a):
            g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1", price=5)
            g.price = 7
            g.save()
            g.delete()
            Garment.deleted_objects.get(pk=g.pk).restore()
        events = list(AuditLog.objects.filter(model_label=Garment._meta.label).order_by("pk").values_list("event", flat=True))
        self.assertEqual(events, ["create", "update", "delete", "restore"])
        update = AuditLog.objects.get(event="update", model_label=Garment._meta.label)
        self.assertEqual(update.changes["price"], ["5.00", "7.00"])
        self.assertEqual(update.user, user)
        self.assertEqual(update.ip_address, "10.0.0.1")

    def test_saving_an_equal_value_of_another_type_logs_no_change(self):
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1", price=5)
        g = Garment.objects.get(pk=g.pk)
        g.price = 5                                                         # int 5 vs stored Decimal("5.00")
        g.save()
        self.assertFalse(AuditLog.objects.filter(event="update", model_label=Garment._meta.label).exists())

    def test_secrets_never_reach_the_log(self):
        g = Garment.objects.create(company=self.company_a, name="x", style_no="s", secret_note="not-sensitive-by-name")
        user = make_user("s@example.com", self.company_a)
        user.set_password("Totally-Secret-9")
        user.save()
        blob = " ".join(str(c.changes) + c.message for c in AuditLog.objects.all())
        self.assertNotIn("Totally-Secret-9", blob)
        self.assertNotIn("pbkdf2", blob)
        self.assertNotIn("md5$", blob)
        row = AuditLog.objects.filter(model_label="core.user", event="update").first()
        if row:
            self.assertEqual(row.changes.get("password", [MASK, MASK])[-1], MASK)

    def test_audit_rows_are_append_only(self):
        Garment.objects.create(company=self.company_a, name="x", style_no="s")
        row = AuditLog.objects.first()
        with self.assertRaises(PermissionError):
            row.save()
        with self.assertRaises(PermissionError):
            row.delete()

    def test_suppressed_context_writes_nothing(self):
        with context.audit_suppressed():
            Garment.objects.create(company=self.company_a, name="quiet", style_no="q")
        self.assertFalse(AuditLog.objects.filter(model_label=Garment._meta.label).exists())

    def test_audit_ui_is_company_scoped_and_permission_checked(self):
        from django.urls import reverse

        viewer = make_user("v@example.com", self.company_a)
        self.assertEqual(self.login(viewer).get(reverse("core:resource_list", args=["audit"])).status_code, 403)
