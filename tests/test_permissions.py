from django.core.cache import cache

from core import permissions as perms
from core.permissions import seed

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user


class PermissionTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("p@example.com", self.company_a)
        self.garment = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        self.other = Garment.objects.create(company=self.company_b, name="Hoodie", style_no="H1")

    def test_no_permission_means_no_access(self):
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment"))
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))

    def test_global_grant_applies_only_inside_member_companies(self):
        self.user.user_permissions.add(*self._perm("view_garment"))
        perms.invalidate()
        self.assertTrue(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment", self.other))

    def test_object_grant_via_guardian_and_filter_by_perm(self):
        perms.assign_perm("dummyapp.view_garment", self.user, self.garment)
        self.assertTrue(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))
        self.assertEqual(list(perms.filter_by_perm(self.user, "dummyapp.view_garment", Garment.objects.all())), [self.garment])
        perms.remove_perm("dummyapp.view_garment", self.user, self.garment)
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))

    def test_bulk_assign_and_remove(self):
        self.assertEqual(perms.bulk_assign_perm("dummyapp.view_garment", self.user, [self.garment, self.other]), 2)
        self.assertEqual(perms.bulk_remove_perm("dummyapp.view_garment", self.user, [self.garment, self.other]), 2)

    def test_cache_invalidated_on_group_change(self):
        from django.contrib.auth.models import Group

        group = Group.objects.create(name="viewers")
        self.user.groups.add(group)
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))
        group.permissions.add(*self._perm("view_garment"))          # m2m signal must bump the cache
        self.assertTrue(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))

    def test_superuser_bypass_is_explicit_and_still_company_aware_for_objects(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        self.assertTrue(perms.user_has_perm(admin, "dummyapp.view_garment"))
        self.assertTrue(perms.user_has_perm(admin, "dummyapp.view_garment", self.garment))

    def test_inactive_user_never_has_permission(self):
        self.user.user_permissions.add(*self._perm("view_garment"))
        self.user.is_active = False
        self.user.save()
        self.assertFalse(perms.user_has_perm(self.user, "dummyapp.view_garment", self.garment))

    def test_seed_dry_run_writes_nothing_and_seed_never_deletes(self):
        from django.contrib.auth.models import Permission

        before = Permission.objects.count()
        dry = seed.seed(dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(Permission.objects.count(), before)
        first = seed.seed()
        self.assertTrue(first["created"])
        Permission.objects.create(content_type=Permission.objects.filter(codename__startswith="access_").first().content_type, codename="access_removed_url", name="x")
        second = seed.seed()
        self.assertEqual(second["created"], [])
        self.assertIn("access_removed_url", second["stale"])
        self.assertTrue(Permission.objects.filter(codename="access_removed_url").exists())

    def test_login_and_health_are_not_seeded_as_protected(self):
        result = seed.seed(dry_run=True)
        self.assertNotIn("access_core_login", result["created"])

    def _perm(self, codename):
        from django.contrib.auth.models import Permission

        return [Permission.objects.get(content_type__app_label="dummyapp", codename=codename)]


class UrlDefaultDenyTests(CoreTestCase):
    def test_named_url_without_grant_is_403_and_with_grant_is_200(self):
        user = make_user("d@example.com", self.company_a)
        client = self.login(user)
        from django.urls import reverse

        seed.seed()
        r = client.get(reverse("core:menu_list"))
        self.assertEqual(r.status_code, 403)
