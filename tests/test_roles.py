"""Roles (auth groups) and permissions pages, the many-to-many widget option and tabbed forms."""
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from core.crud.forms import m2m_widgets_for
from core.crud.widgets import CheckboxList
from core.menu.service import invalidate_menus, menu_for
from core.models import ApprovalType, Role

from .base import CoreTestCase, make_user

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "core-modal-content"}


def perm(full):
    app, code = full.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=code)


class RoleTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("staff@example.com", self.company_a, perms=["dummyapp.view_garment"])
        self.staff.is_staff = True
        self.staff.save()
        self.root = make_user("root@example.com", self.company_a, superuser=True)
        self.member = make_user("member@example.com", self.company_a)

    def test_staff_can_add_a_role_with_permissions_they_hold(self):
        client = self.login(self.staff)
        self.assertEqual(client.get(reverse("core:roles")).status_code, 200)
        form = client.get(reverse("core:resource_new", args=["roles"]), **MODAL).content.decode()
        self.assertIn("nav-tabs", form)                                    # Role / Permissions / Members tabs
        held, other = perm("dummyapp.view_garment"), perm("dummyapp.delete_garment")
        self.assertIn(f'value="{held.pk}"', form)
        self.assertNotIn(f'value="{other.pk}"', form)                      # not offered: the editor does not hold it
        r = client.post(reverse("core:resource_new", args=["roles"]),
                        {"name": "Viewers", "permissions": [held.pk], "members": [self.member.pk]}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content.decode()[:500])
        role = Group.objects.get(name="Viewers")
        self.assertEqual(list(role.permissions.all()), [held])
        self.assertEqual(list(role.user_set.all()), [self.member])

    def test_staff_cannot_grant_a_permission_they_lack(self):
        r = self.login(self.staff).post(reverse("core:resource_new", args=["roles"]),
                                        {"name": "Sneaky", "permissions": [perm("dummyapp.delete_garment").pk]}, **MODAL)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Group.objects.filter(name="Sneaky").exists())

    def test_members_of_a_stronger_role_are_locked_for_staff_and_hidden_permissions_are_kept(self):
        strong = Role.objects.create(name="Managers")
        strong.permissions.add(perm("dummyapp.delete_garment"))
        r = self.login(self.staff).post(reverse("core:resource_edit", args=["roles", strong.pk]),
                                        {"name": "Managers 2", "permissions": [perm("dummyapp.view_garment").pk],
                                         "members": [self.staff.pk]}, **MODAL)
        self.assertEqual(r.status_code, 204, r.content.decode()[:500])
        strong.refresh_from_db()
        self.assertEqual(strong.name, "Managers 2")
        self.assertEqual({p.codename for p in strong.permissions.all()}, {"delete_garment", "view_garment"})
        self.assertFalse(strong.user_set.exists())                          # staff could not add themselves to it

    def test_only_superusers_delete_roles(self):
        role = Role.objects.create(name="Temp")
        url = reverse("core:resource_delete", args=["roles", role.pk])
        self.assertIn(self.login(self.staff).post(url, **MODAL).status_code, (403, 400, 200))
        self.assertTrue(Group.objects.filter(pk=role.pk).exists())
        r = self.login(self.root).post(url, **MODAL)
        self.assertEqual(r.status_code, 204, r.get("Location"))
        self.assertFalse(Group.objects.filter(pk=role.pk).exists())

    def test_permissions_page_is_read_only_and_staff_only(self):
        self.assertEqual(self.login(self.staff).get(reverse("core:permissions")).status_code, 200)
        self.assertEqual(self.login(self.staff).get(reverse("core:resource_new", args=["permissions"])).status_code, 403)
        self.assertEqual(self.login(self.member).get(reverse("core:permissions")).status_code, 403)
        self.assertEqual(self.login(self.member).get(reverse("core:roles")).status_code, 403)

    def test_staff_see_the_staff_and_superuser_entries_of_administration(self):
        from core.menu.defaults import seed
        from core.models import MenuItem

        seed(MenuItem)
        invalidate_menus()
        titles = lambda user: {e.title: [c.title for c in e.children] for e in menu_for(user, self.company_a.pk)}  # noqa: E731
        staff_menu = titles(self.staff)
        self.assertEqual(staff_menu.get("Administration"), ["Common", "Users", "Auth", "Notification Rules", "Backups", "Archive",
                                                            "Export jobs", "Background jobs", "Audit log"])
        admin_entry = next(e for e in menu_for(self.staff, self.company_a.pk) if e.title == "Administration")
        children = lambda title: [c.title for c in next(c for c in admin_entry.children if c.title == title).children]  # noqa: E731
        self.assertEqual(children("Auth"), ["Permissions", "Roles"])
        self.assertEqual(children("Common"), ["Departments", "Designations"])           # Companies stays superuser-only
        self.assertNotIn("Administration", titles(self.member))
        self.assertEqual(titles(self.root)["Administration"], ["Common", "Users", "Menu Builder", "Approval Builder", "Auth",
                                                               "Notification Rules", "Saved Reports", "Search builder", "Backups",
                                                               "Archive", "Export jobs", "Background jobs", "Audit log"])

    def test_staff_open_their_admin_pages_but_not_the_superuser_ones(self):
        from core.permissions import user_has_perm

        client = self.login(self.staff)
        for name in ("core:departments", "core:designations", "core:notification_rules", "core:backup_list", "core:archive_list",
                     "core:export_jobs", "core:users"):
            self.assertEqual(client.get(reverse(name)).status_code, 200, name)
        for name in ("core:companies", "core:menu_list", "core:approval_types", "core:search_builder"):
            self.assertEqual(client.get(reverse(name)).status_code, 403, name)
        self.assertFalse(user_has_perm(self.staff, "core.restore_backup"))              # restores stay with superusers
        self.assertFalse(user_has_perm(self.staff, "core.restore_archive"))
        self.assertEqual(self.login(self.member).get(reverse("core:departments")).status_code, 403)


class WidgetAndTabTests(CoreTestCase):
    def test_m2m_widget_choice(self):
        self.assertEqual(m2m_widgets_for(ApprovalType, ["name", "notify_users"], {"notify_users": "checkbox"}),
                         {"notify_users": CheckboxList})
        with self.settings(CORE_M2M_WIDGET="checkbox"):
            self.assertEqual(m2m_widgets_for(ApprovalType, ["notify_groups"], {})["notify_groups"], CheckboxList)

    def test_bad_registrations_are_refused(self):
        from core.crud.registry import register_resource
        from tests.dummyapp.models import OldRecord

        with self.assertRaises(ImproperlyConfigured):
            register_resource(OldRecord, slug="bad-m2m", m2m_widgets={"payload": "checkbox"})
        with self.assertRaises(ImproperlyConfigured):
            register_resource(OldRecord, slug="bad-tab", tabs=(("Lines", ("@nothing",)),))
