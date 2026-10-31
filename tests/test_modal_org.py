"""Modal-only CRUD, the extended user profile and the company hierarchy."""
import io
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from PIL import Image

from core.models import Company, Department, Designation, Profile

from .base import CoreTestCase, make_user

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "core-modal-content"}
PASSWORD = "Zx9-correct-horse-battery"


def png(name="x.png"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class ModalCrudTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("modal-root@example.com", self.company_a, superuser=True)
        self.client = self.login(self.admin)

    def test_form_is_a_partial_in_the_modal_and_a_page_otherwise(self):
        url = reverse("core:resource_new", args=["departments"])
        partial = self.client.get(url, **MODAL)
        self.assertContains(partial, 'hx-post="')
        self.assertNotContains(partial, "<html")
        self.assertContains(self.client.get(url), "<html")                 # the endpoint still works as a page (tests / API-style use)

    def test_list_pages_only_have_buttons_for_create_edit_view_delete(self):
        dept = Department.objects.create(name="Ops")
        body = self.client.get(reverse("core:resource_list", args=["departments"])).content.decode()
        for name, args in (("resource_new", ["departments"]), ("resource_edit", ["departments", dept.pk]),
                           ("resource_detail", ["departments", dept.pk]), ("resource_delete", ["departments", dept.pk])):
            url = reverse(f"core:{name}", args=args)
            self.assertIn(f'hx-get="{url}"', body, name)
            self.assertNotIn(f'href="{url}"', body, name)

    def test_invalid_post_returns_field_errors_inside_the_modal(self):
        r = self.client.post(reverse("core:resource_new", args=["departments"]), {"name": ""}, **MODAL)
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "This field is required", status_code=400)
        self.assertContains(r, "core-modal-content", status_code=400)

    def test_valid_post_closes_the_modal_via_header(self):
        r = self.client.post(reverse("core:resource_new", args=["departments"]), {"name": "Ops", "is_active": "on"}, **MODAL)
        self.assertEqual(r.status_code, 204)
        event = json.loads(r["HX-Trigger"])["core:modal-done"]
        self.assertEqual(event["level"], "success")
        self.assertTrue(Department.objects.filter(name="Ops").exists())

    def test_duplicate_name_is_a_form_error_not_a_server_error(self):
        Department.objects.create(name="Ops")
        r = self.client.post(reverse("core:resource_new", args=["departments"]), {"name": "Ops", "is_active": "on"}, **MODAL)
        self.assertEqual(r.status_code, 400)

    def test_department_and_designation_have_no_code_field(self):
        for slug in ("departments", "designations"):
            body = self.client.get(reverse("core:resource_new", args=[slug]), **MODAL).content.decode()
            self.assertNotIn('name="code"', body)

    def test_department_and_designation_are_shared_by_every_company(self):
        self.assertFalse(any(f.name == "company" for model in (Department, Designation) for f in model._meta.get_fields()))
        Department.objects.create(name="Ops")
        Designation.objects.create(name="Manager")
        member_b = make_user("modal-b@example.com", self.company_b, superuser=True)
        client = self.login(member_b)
        self.assertContains(client.get(reverse("core:resource_list", args=["departments"])), "Ops")
        self.assertContains(client.get(reverse("core:resource_list", args=["designations"])), "Manager")
        with self.assertRaises(ValidationError):                    # names are unique overall now (every engine: model validation)
            Designation(name="Manager").full_clean()
        if connection.features.supports_partial_indexes:            # PostgreSQL / SQLite also enforce it in the database
            with self.assertRaises(IntegrityError), transaction.atomic():
                Designation.objects.create(name="Manager")

    def test_delete_uses_a_confirmation_modal(self):
        dept = Department.objects.create(name="Temp")
        url = reverse("core:resource_delete", args=["departments", dept.pk])
        self.assertContains(self.client.get(url, **MODAL), "Delete department?")
        self.assertEqual(self.client.post(url, **MODAL).status_code, 204)
        self.assertFalse(Department.objects.filter(pk=dept.pk).exists())

    def test_detail_modal(self):
        dept = Department.objects.create(name="Ops")
        r = self.client.get(reverse("core:resource_detail", args=["departments", dept.pk]), **MODAL)
        self.assertContains(r, "Ops")
        self.assertNotContains(r, "<html")

    def test_menu_builder_uses_modals_too(self):
        r = self.client.get(reverse("core:menu_new"), **MODAL)
        self.assertContains(r, 'hx-post="')
        bad = self.client.post(reverse("core:menu_new"), {"title": ""}, **MODAL)
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(reverse("core:menu_new"), {"title": "Docs", "path": "/r/companies/", "permission": "core.view_company", "position": "5",
                                                              "is_active": "on"}, **MODAL)
        self.assertEqual(ok.status_code, 204)
        from core.models import MenuItem

        mi = MenuItem.objects.get(title="Docs")
        self.assertContains(self.client.get(reverse("core:menu_delete", args=[mi.pk]), **MODAL), "Delete menu item?")
        self.assertEqual(self.client.post(reverse("core:menu_delete", args=[mi.pk]), **MODAL).status_code, 204)
        self.assertFalse(MenuItem.objects.filter(pk=mi.pk).exists())

    def test_menu_links_need_a_real_permission_except_dashboard(self):
        post = lambda **data: self.client.post(reverse("core:menu_new"), {"position": "5", "is_active": "on", **data}, **MODAL)
        self.assertContains(post(title="Docs", path="/r/companies/"), "needs a permission", status_code=400)
        self.assertContains(post(title="Docs", path="/r/companies/", permission="core.view_nothing"), "No permission", status_code=400)
        self.assertEqual(post(title="Home", url_name="core:dashboard").status_code, 204)
        self.assertEqual(post(title="Staff", url_name="core:users", permission="core.is_staff").status_code, 204)


class CompanyHierarchyTests(CoreTestCase):
    def make(self, name, **extra):
        return Company.objects.create(name=name, abbreviation=name[:3].upper(), address="Somewhere", **extra)

    def test_new_fields_exist(self):
        for name in ("is_parent", "parent", "name", "abbreviation", "address", "factory_address", "email", "phone", "fax", "logo", "website"):
            Company._meta.get_field(name)
        self.assertEqual(Company._meta.get_field("name").max_length, 255)
        self.assertEqual(Company._meta.get_field("abbreviation").max_length, 50)

    def test_child_marks_its_parent_as_parent(self):
        parent = self.make("Group")
        self.make("Child", parent=parent)
        parent.refresh_from_db()
        self.assertTrue(parent.is_parent)

    def test_rules(self):
        parent = self.make("Group", is_parent=True)
        child = self.make("Child", parent=parent)
        with self.assertRaises(ValidationError):                                    # cannot be its own parent
            Company(pk=parent.pk, name="x", abbreviation="x", address="x", parent=parent, is_parent=True).clean()
        grandchild = Company(name="Grand", abbreviation="G", address="x", parent=child)
        with self.assertRaises(ValidationError):                                    # only two levels
            grandchild.clean()
        child.is_parent = True
        with self.assertRaises(ValidationError):                                    # a child cannot be a parent company
            child.clean()
        child.is_parent = False
        parent.is_parent = False
        with self.assertRaises(ValidationError):                                    # has children => must be a parent
            parent.clean()
        parent.is_parent, parent.parent = True, None
        parent.clean()

    def test_company_with_children_cannot_move_under_another_company(self):
        parent, other = self.make("Group", is_parent=True), self.make("Other", is_parent=True)
        self.make("Child", parent=parent)
        parent.parent = other
        with self.assertRaises(ValidationError):
            parent.clean()

    def test_database_constraint_backs_the_rule(self):
        parent = self.make("Group", is_parent=True)
        child = self.make("Child", parent=parent)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Company.objects.filter(pk=child.pk).update(is_parent=True)

    def test_delete_with_children_shows_a_friendly_message_in_the_modal(self):
        admin = make_user("hier-root@example.com", self.company_a, superuser=True)
        client = self.login(admin)
        parent = self.make("Group", is_parent=True)
        child = self.make("Child", parent=parent)
        url = reverse("core:resource_delete", args=["companies", parent.pk])
        r = client.post(url, **MODAL)
        self.assertContains(r, "child company", status_code=400)
        self.assertContains(r, "Child", status_code=400)
        self.assertTrue(Company.objects.filter(pk=parent.pk).exists())
        self.assertEqual(client.post(reverse("core:resource_delete", args=["companies", child.pk]), **MODAL).status_code, 204)
        self.assertEqual(client.post(url, **MODAL).status_code, 204)

    def test_company_modal_form_shows_the_hierarchy_and_saves_a_logo(self):
        admin = make_user("hier2@example.com", self.company_a, superuser=True)
        client = self.login(admin)
        parent = self.make("Group", is_parent=True)
        self.make("Child", parent=parent)
        page = client.get(reverse("core:resource_new", args=["companies"]), **MODAL)
        self.assertContains(page, "└")                                              # child shown indented under its parent
        self.assertContains(page, "disabled")
        r = client.post(reverse("core:resource_new", args=["companies"]), {
            "name": "Sub", "abbreviation": "SUB", "parent": parent.pk, "address": "Road 1", "factory_address": "", "email": "a@b.co", "phone": "1",
            "fax": "", "website": "https://example.com", "is_active": "on", "logo": png("logo.png")}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:400])
        sub = Company.objects.get(name="Sub")
        self.assertEqual(sub.parent_id, parent.pk)
        self.assertTrue(sub.logo)

    def test_company_form_rejects_invalid_hierarchy(self):
        admin = make_user("hier3@example.com", self.company_a, superuser=True)
        client = self.login(admin)
        parent = self.make("Group", is_parent=True)
        child = self.make("Child", parent=parent)
        r = client.post(reverse("core:resource_new", args=["companies"]), {"name": "Deep", "abbreviation": "D", "parent": child.pk, "address": "x", "is_active": "on"}, **MODAL)
        self.assertEqual(r.status_code, 400)
        r = client.post(reverse("core:resource_new", args=["companies"]), {"name": "Both", "abbreviation": "B", "parent": parent.pk, "is_parent": "on", "address": "x", "is_active": "on"}, **MODAL)
        self.assertEqual(r.status_code, 400)


class UserProfileTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Ops")
        self.desig = Designation.objects.create(name="Manager")
        self.admin = make_user("users-root@example.com", self.company_a, superuser=True)
        self.client = self.login(self.admin)

    def payload(self, **extra):
        base = {"email": "new.user@example.com", "full_name": "New User", "password1": PASSWORD, "password2": PASSWORD,
                "default_company": self.company_a.pk, "companies": [self.company_a.pk], "dept": self.dept.pk, "designation": self.desig.pk,
                "official_id": "E-100", "joining_date": "2025-02-03", "contact_no": "01700000000", "is_active": "on"}
        base.update(extra)
        return base

    def test_profile_fields_are_mandatory_and_typed(self):
        for name in ("dept", "designation", "default_company"):
            field = Profile._meta.get_field(name)
            self.assertFalse(field.null, name)
            self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT", name)
        for name in ("official_id", "joining_date", "contact_no", "user_img", "user_sign"):
            Profile._meta.get_field(name)
        self.assertEqual(Profile._meta.get_field("user_img").upload_to, "users/img/")
        self.assertEqual(Profile._meta.get_field("user_sign").upload_to, "users/sign/")

    def test_a_user_can_be_added_from_the_modal(self):
        form = self.client.get(reverse("core:resource_new", args=["users"]), **MODAL)
        self.assertContains(form, 'name="password1"')
        self.assertContains(form, 'name="dept"')
        r = self.client.post(reverse("core:resource_new", args=["users"]), {**self.payload(), "user_img": png("me.png"), "user_sign": png("sig.png")}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:600])
        user = get_user_model().objects.get(email="new.user@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        p = user.profile
        self.assertEqual((p.dept_id, p.designation_id, p.default_company_id), (self.dept.pk, self.desig.pk, self.company_a.pk))
        self.assertEqual((p.official_id, str(p.joining_date), p.contact_no), ("E-100", "2025-02-03", "01700000000"))
        self.assertTrue(p.user_img and p.user_sign)
        self.assertIn(self.company_a.pk, p.member_company_ids())
        self.assertEqual(self.client.get(reverse("core:user_file", args=[user.pk, "user_img"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:user_file", args=[user.pk, "password"])).status_code, 404)

    def test_default_company_is_always_a_member(self):
        r = self.client.post(reverse("core:resource_new", args=["users"]), self.payload(companies=[]), **MODAL)
        self.assertEqual(r.status_code, 204)
        self.assertIn(self.company_a.pk, get_user_model().objects.get(email="new.user@example.com").profile.member_company_ids())

    def test_validation_errors_stay_in_the_modal(self):
        bad = self.payload(password2="different", dept="")
        r = self.client.post(reverse("core:resource_new", args=["users"]), bad, **MODAL)
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "do not match", status_code=400)
        self.assertFalse(get_user_model().objects.filter(email="new.user@example.com").exists())
        dup = self.client.post(reverse("core:resource_new", args=["users"]), self.payload(email=self.admin.email), **MODAL)
        self.assertEqual(dup.status_code, 400)

    def test_edit_updates_the_profile_and_keeps_the_password(self):
        self.client.post(reverse("core:resource_new", args=["users"]), self.payload(), **MODAL)
        user = get_user_model().objects.get(email="new.user@example.com")
        other = Department.objects.create(name="Sales")
        r = self.client.post(reverse("core:resource_edit", args=["users", user.pk]),
                             {"email": user.email, "full_name": "Renamed", "default_company": self.company_a.pk, "companies": [self.company_a.pk],
                              "dept": other.pk, "designation": self.desig.pk, "official_id": "E-1", "contact_no": "1", "is_active": "on"}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:500])
        user.refresh_from_db()
        self.assertEqual((user.full_name, user.profile.dept_id), ("Renamed", other.pk))
        self.assertTrue(user.check_password(PASSWORD))

    def test_editors_grant_only_roles_they_could_hold_and_cannot_edit_superusers(self):
        from django.contrib.auth.models import Group, Permission

        manager = make_user("hr@example.com", self.company_a, perms=["core.view_user", "core.add_user", "core.change_user"])
        weak = Group.objects.create(name="Weak")
        weak.permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_user"))
        strong = Group.objects.create(name="Strong")
        strong.permissions.add(Permission.objects.get(content_type__app_label="core", codename="delete_company"))
        client = self.login(manager)
        page = client.get(reverse("core:resource_new", args=["users"]), **MODAL).content.decode()
        self.assertIn('name="dept"', page)
        self.assertIn(f'name="groups" value="{weak.pk}"', page)
        self.assertNotIn(f'name="groups" value="{strong.pk}"', page)       # holds a permission the editor lacks
        self.assertEqual(client.get(reverse("core:resource_edit", args=["users", self.admin.pk]), **MODAL).status_code, 403)

    def test_a_user_created_outside_the_form_still_gets_a_complete_profile(self):
        user = get_user_model().objects.create_user("script@example.com", PASSWORD)
        p = Profile.objects.get(user=user)
        self.assertIsNotNone(p.dept_id and p.designation_id and p.default_company_id)
        self.assertEqual(p.member_company_ids(), set())                             # a placeholder profile never grants company access

    def test_cannot_deactivate_yourself(self):
        r = self.client.post(reverse("core:resource_edit", args=["users", self.admin.pk]),
                             {"email": self.admin.email, "full_name": "", "default_company": self.company_a.pk, "companies": [self.company_a.pk],
                              "dept": self.admin.profile.dept_id, "designation": self.admin.profile.designation_id}, **MODAL)
        self.assertEqual(r.status_code, 400)
