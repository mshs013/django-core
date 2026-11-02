"""Audit of many-to-many / permission changes (core.audit.m2m), bulk paths and sign-in events."""
from django.contrib.auth.models import Permission
from django.urls import reverse

from core.audit.display import render_changes
from core.models import AuditLog, Company, Department, Designation, Role
from core.permissions import assign_perm, remove_perm
from core.services import context

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_company, make_user
from .test_modal_org import MODAL, PASSWORD


def perm(code):
    app, codename = code.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


class M2MAuditTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        with self.captureOnCommitCallbacks(execute=True):    # the fixtures' own m2m changes are committed (and logged) first
            self.actor = make_user("actor@example.com", self.company_a, superuser=True)
            self.user = make_user("member@example.com", self.company_a, page_perms=False)
        self.role = Role.objects.create(name="Clerks")

    def rows(self, **filters):
        return AuditLog.objects.filter(model_label="core.User", object_pk=str(self.user.pk), **filters).order_by("pk")

    def change(self, call):
        """Run ``call`` as the actor and let the commit hooks fire (one audit row per operation is written at commit)."""
        before = AuditLog.objects.count()
        with context.acting_as(self.actor, "10.1.2.3", frozenset({self.company_a.pk})), self.captureOnCommitCallbacks(execute=True):
            call()
        return AuditLog.objects.filter(pk__gt=AuditLog.objects.order_by("pk")[before - 1].pk if before else 0).order_by("pk")

    def test_adding_and_removing_a_company_is_one_row_on_the_user(self):
        new = self.change(lambda: self.user.profile.companies.add(self.company_b))
        self.assertEqual([(r.event, r.model_label) for r in new], [("update", "core.User")])
        row = new[0]
        self.assertEqual(row.changes, {"companies": [["Company A"], ["Company A", "Company B"]]})
        self.assertEqual((row.user, row.ip_address, row.company_id), (self.actor, "10.1.2.3", self.company_a.pk))
        self.assertIn("Member of", str(render_changes(row)))
        self.assertIn("added Company B", row.message)
        new = self.change(lambda: self.user.profile.companies.remove(self.company_b))
        self.assertEqual(new.count(), 1)
        self.assertEqual(new[0].changes["companies"], [["Company A", "Company B"], ["Company A"]])

    def test_set_with_adds_and_removes_is_one_row_and_no_change_is_no_row(self):
        new = self.change(lambda: self.user.profile.companies.set([self.company_b]))          # remove A + add B
        self.assertEqual(new.count(), 1)
        self.assertEqual(new[0].changes["companies"], [["Company A"], ["Company B"]])
        self.assertEqual(self.change(lambda: self.user.profile.companies.set([self.company_b])).count(), 0)
        self.assertEqual(self.change(lambda: self.user.profile.companies.add(self.company_b)).count(), 0)

    def test_role_and_user_permission_are_permission_rows_on_the_user(self):
        new = self.change(lambda: self.user.groups.add(self.role))
        self.assertEqual([(r.event, r.changes) for r in new], [("permission", {"groups": [[], ["Clerks"]]})])
        self.assertIn("Roles", str(render_changes(new[0])))
        new = self.change(lambda: self.user.user_permissions.add(perm("core.add_user")))
        self.assertEqual(new.count(), 1)
        self.assertEqual(new[0].changes, {"user_permissions": [[], ["core.add_user"]]})
        self.assertIn("core.add_user", str(render_changes(new[0])))

    def test_role_permission_is_one_row_on_the_role(self):
        new = self.change(lambda: self.role.permissions.add(perm("core.view_user"), perm("core.change_user")))
        self.assertEqual([(r.event, r.model_label, r.object_pk) for r in new], [("permission", "core.Role", str(self.role.pk))])
        self.assertEqual(new[0].changes, {"permissions": [[], ["core.change_user", "core.view_user"]]})
        new = self.change(lambda: self.role.permissions.clear())
        self.assertEqual(new[0].changes["permissions"][1], [])

    def test_object_permission_names_who_what_and_object(self):
        garment = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        new = self.change(lambda: assign_perm("dummyapp.change_garment", self.user, garment))
        row = new.filter(event="permission").get()
        self.assertEqual((row.user, row.model_label, row.object_pk), (self.actor, Garment._meta.label, str(garment.pk)))
        self.assertIn("assign dummyapp.change_garment -> member@example.com", row.message)
        new = self.change(lambda: remove_perm("dummyapp.change_garment", self.user, garment))
        self.assertTrue(new.filter(event="permission", message__startswith="remove").exists())

    def test_rolled_back_change_writes_nothing(self):
        from django.db import transaction

        def call():
            try:
                with transaction.atomic():
                    self.user.groups.add(self.role)
                    raise RuntimeError
            except RuntimeError:
                pass

        self.assertEqual(self.change(call).count(), 0)

    def test_bulk_update_of_an_audited_model_is_logged(self):
        parent = make_company("P")
        Company.objects.filter(pk=parent.pk).update(is_parent=False)
        with context.acting_as(self.actor):
            Company.objects.create(abbreviation="K", name="Kid", address="x", parent=parent)
        row = AuditLog.objects.filter(model_label="core.Company", object_pk=str(parent.pk), event="update").latest("pk")
        self.assertEqual(row.changes["is_parent"], [False, True])


class UserFormAuditTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Ops")
        self.desig = Designation.objects.create(name="Manager")
        self.root = make_user("root-a@example.com", self.company_a, superuser=True)
        self.role = Role.objects.create(name="Viewers")

    def test_user_form_writes_one_readable_row_per_changed_list_and_no_secrets(self):
        client = self.login(self.root)
        url = reverse("core:resource_new", args=["users"])
        data = {"email": "new@example.com", "full_name": "New", "password1": PASSWORD, "password2": PASSWORD,
                "default_company": self.company_a.pk, "companies": [self.company_a.pk], "dept": self.dept.pk, "designation": self.desig.pk,
                "is_active": "on"}
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(client.post(url, data, **MODAL).status_code, 204)
        user = type(self.root).objects.get(email="new@example.com")
        edit = reverse("core:resource_edit", args=["users", user.pk])
        before = AuditLog.objects.order_by("-pk").first().pk
        data.pop("password1"), data.pop("password2")
        with self.captureOnCommitCallbacks(execute=True):
            r = client.post(edit, {**data, "companies": [self.company_a.pk, self.company_b.pk], "groups": [self.role.pk],
                                   "user_permissions": [perm("core.view_user").pk]}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:300])
        rows = AuditLog.objects.filter(pk__gt=before, model_label="core.User", object_pk=str(user.pk))
        by_field = {}
        for row in rows:
            for key in row.changes:
                by_field.setdefault(key, []).append(row)
        self.assertEqual({k: len(v) for k, v in by_field.items() if k in ("companies", "groups", "user_permissions")},
                         {"companies": 1, "groups": 1, "user_permissions": 1})
        self.assertEqual(by_field["groups"][0].changes["groups"], [[], ["Viewers"]])
        with self.captureOnCommitCallbacks(execute=True):                                        # same values again: no new rows
            client.post(edit, {**data, "companies": [self.company_a.pk, self.company_b.pk], "groups": [self.role.pk],
                               "user_permissions": [perm("core.view_user").pk]}, **MODAL)
        last = AuditLog.objects.order_by("-pk").first().pk
        self.assertFalse(AuditLog.objects.filter(pk__gt=rows.order_by("-pk").first().pk, pk__lte=last, model_label="core.User",
                                                 object_pk=str(user.pk), event__in=["update", "permission"]).exists())
        blob = " ".join(str(r.changes) + str(r.snapshot) + r.message for r in AuditLog.objects.all())
        self.assertNotIn(PASSWORD, blob)
        self.assertNotIn("pbkdf2", blob)


class SignInAuditTests(CoreTestCase):
    def test_login_failed_login_and_logout_rows(self):
        user = make_user("signin@example.com", self.company_a)
        client = self.login(user)                                   # Client.login does not go through the view
        client.logout()
        client.post(reverse("core:login"), {"email": "signin@example.com", "password": "wrong-password"}, REMOTE_ADDR="10.9.8.7")
        client.post(reverse("core:login"), {"email": "signin@example.com", "password": self.password}, REMOTE_ADDR="10.9.8.7")
        client.post(reverse("core:logout"), REMOTE_ADDR="10.9.8.7")
        failed = AuditLog.objects.get(event="login_failed")
        self.assertEqual((failed.user, failed.user_repr, failed.ip_address), (None, "signin@example.com", "10.9.8.7"))
        for event in ("login", "logout"):
            row = AuditLog.objects.get(event=event)
            self.assertEqual((row.user, row.ip_address, row.company_id), (user, "10.9.8.7", self.company_a.pk), event)
        blob = " ".join(str(r.changes) + r.message for r in AuditLog.objects.all())
        self.assertNotIn("wrong-password", blob)
        self.assertNotIn(self.password, blob)

    def test_password_change_is_logged(self):
        user = make_user("pw@example.com", self.company_a)
        client = self.login(user)
        client.post(reverse("core:password_change"), {"old_password": self.password, "new_password1": "Another-Pass-9876",
                                                       "new_password2": "Another-Pass-9876"})
        row = AuditLog.objects.get(message="password changed")
        self.assertEqual((row.user, row.company_id), (user, self.company_a.pk))
        self.assertNotIn("Another-Pass-9876", str(row.changes) + row.message)
