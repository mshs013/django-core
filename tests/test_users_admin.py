"""User stamps + soft delete, staff/superuser flags, image thumbnails and previews, dotted permission search."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from core.crud.registry import get_resource
from core.models import Company, Department, Designation
from core.services import context

from .base import CoreTestCase, make_user
from .test_modal_org import MODAL, PASSWORD, png


class UserAdminTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Ops")
        self.desig = Designation.objects.create(name="Manager")
        self.root = make_user("root6@example.com", self.company_a, superuser=True)
        self.staff = make_user("staff6@example.com", self.company_a)
        self.staff.is_staff = True
        self.staff.save()

    def payload(self, **extra):
        return {"email": "made@example.com", "full_name": "Made", "password1": PASSWORD, "password2": PASSWORD,
                "default_company": self.company_a.pk, "companies": [self.company_a.pk], "dept": self.dept.pk,
                "designation": self.desig.pk, "is_active": "on", **extra}

    def test_user_has_created_updated_and_soft_delete_stamps(self):
        User = get_user_model()
        for name in ("created_by", "created_at", "updated_by", "updated_at", "deleted_by", "deleted_at"):
            User._meta.get_field(name)
        r = self.login(self.root).post(reverse("core:resource_new", args=["users"]), self.payload(), **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:500])
        made = User.objects.get(email="made@example.com")
        self.assertEqual((made.created_by, made.updated_by), (self.root, self.root))
        self.assertFalse(made.is_deleted)
        with context.acting_as(self.root):
            made.delete()
        self.assertFalse(User.objects.filter(pk=made.pk).exists())             # default manager hides deleted accounts
        gone = User.global_objects.get(pk=made.pk)
        self.assertTrue(gone.is_deleted)
        self.assertEqual(gone.deleted_by, self.root)
        self.assertFalse(self.client.login(email="made@example.com", password=PASSWORD))
        gone.restore()
        self.assertTrue(User.objects.filter(pk=made.pk).exists())

    def test_only_superusers_set_staff_and_superuser_flags(self):
        form = self.login(self.root).get(reverse("core:resource_new", args=["users"]), **MODAL).content.decode()
        self.assertIn('name="is_staff"', form)
        self.assertIn('name="is_superuser"', form)
        r = self.login(self.root).post(reverse("core:resource_new", args=["users"]), self.payload(is_staff="on"), **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:500])
        self.assertTrue(get_user_model().objects.get(email="made@example.com").is_staff)

    def test_staff_create_normal_users_only(self):
        client = self.login(self.staff)
        self.assertEqual(client.get(reverse("core:users")).status_code, 200)
        form = client.get(reverse("core:resource_new", args=["users"]), **MODAL).content.decode()
        self.assertNotIn('name="is_staff"', form)
        self.assertNotIn('name="is_superuser"', form)
        r = client.post(reverse("core:resource_new", args=["users"]), self.payload(is_staff="on", is_superuser="on"), **MODAL)
        self.assertEqual(r.status_code, 204, r.content[:500])
        made = get_user_model().objects.get(email="made@example.com")
        self.assertFalse(made.is_staff or made.is_superuser)
        self.assertEqual(client.get(reverse("core:resource_edit", args=["users", self.root.pk]), **MODAL).status_code, 403)
        self.assertEqual(self.login(make_user("plain6@example.com", self.company_a)).get(reverse("core:users")).status_code, 403)

    def test_edit_form_shows_stored_photo_and_signature(self):
        client = self.login(self.root)
        client.post(reverse("core:resource_new", args=["users"]), {**self.payload(), "user_img": png("me.png"), "user_sign": png("s.png")}, **MODAL)
        made = get_user_model().objects.get(email="made@example.com")
        form = client.get(reverse("core:resource_edit", args=["users", made.pk]), **MODAL).content.decode()
        self.assertIn(reverse("core:user_file", args=[made.pk, "user_img"]), form)
        self.assertIn(reverse("core:user_file", args=[made.pk, "user_sign"]), form)
        self.assertIn("core-file-preview", form)
        page = client.get(reverse("core:users")).content.decode()                # list: photo thumbnail, no signature column
        self.assertIn(f'<img src="{reverse("core:user_file", args=[made.pk, "user_img"])}"', page)
        self.assertNotIn(reverse("core:user_file", args=[made.pk, "user_sign"]), page)

    def test_list_shows_image_thumbnail(self):
        from core.views.crud import _list_cell

        company = Company.objects.get(pk=self.company_a.pk)
        company.logo = png("logo.png")
        company.save()
        cell = str(_list_cell(get_resource("companies"), company, "logo"))
        self.assertIn("<img", cell)
        self.assertIn(reverse("core:resource_file", args=["companies", company.pk, "logo"]), cell)

    def test_dotted_permission_search(self):
        client = self.login(self.root)
        add_user = Permission.objects.get(content_type__app_label="core", codename="add_user")
        page = client.get(reverse("core:permissions"), {"q": "core.add_user"}).content.decode()
        self.assertIn(add_user.name, page)
        form = client.get(reverse("core:resource_new", args=["roles"]), **MODAL).content.decode()
        self.assertIn("core.add_user", form)                                # codes are shown (and filterable) in the picker



class ProfileAuditTests(CoreTestCase):
    def test_profile_changes_are_audited_but_company_switch_and_theme_are_not(self):
        from core.models import AuditLog

        user = make_user("audited@example.com", self.company_a)
        user.profile.companies.add(self.company_b)
        profile_log = AuditLog.objects.filter(model_label="core.Profile", object_pk=str(user.profile.pk), event=AuditLog.UPDATE)
        client = self.login(user)
        client.post(reverse("core:profile"), {"full_name": user.full_name, "phone": "01700-000000", "theme": "dark"})
        entry = profile_log.get()
        self.assertIn("contact_no", entry.changes)
        self.assertNotIn("theme", entry.changes)
        client.post(reverse("core:switch_company"), {"company": self.company_b.pk})
        client.post(reverse("core:theme"), {"theme": "light"})
        self.assertEqual(profile_log.count(), 1)                     # no noise from switching company / theme

    def test_photo_change_is_audited_as_image_change_only(self):
        from core.audit.display import render_changes
        from core.models import AuditLog

        profile = make_user("photo@example.com", self.company_a).profile
        logs = AuditLog.objects.filter(model_label="core.Profile", object_pk=str(profile.pk), event=AuditLog.UPDATE).order_by("pk")
        profile.user_img = png("face.png")
        profile.save()
        first = logs.last()
        self.assertIn("user_img", first.changes)
        html = str(render_changes(first))
        self.assertIn("Photo: New image", html)
        self.assertNotIn("face", html)                                  # never the file name
        profile.user_img = None
        profile.save()
        self.assertIn("Photo: Image removed", str(render_changes(logs.last())))
        profile.contact_no = "123"
        profile.save()
        self.assertNotIn("user_img", logs.last().changes)              # unchanged image: not flagged
