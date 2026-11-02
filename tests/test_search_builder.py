"""Dynamic Search Builder: superusers build standard searches; everyone else uses them (paginated) without changing them."""
import json

from django.urls import reverse

from core.models import SavedSearch

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user

READY = {"op": "AND", "items": [{"field": "status", "op": "eq", "value": "ready"}]}


class SearchBuilderTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_user("root@example.com", self.company_a, superuser=True)
        self.user = make_user("user@example.com", self.company_a, perms=["dummyapp.view_garment"])
        for n in range(12):
            Garment.objects.create(company=self.company_a, name=f"G{n:02}", style_no=f"S{n}", status="ready" if n % 2 else "draft")
        self.edit_url = reverse("core:search_builder_edit", args=["resource", "garments"])

    def save(self, **extra):
        data = {"name": "Ready garments", "adv": json.dumps(READY), "sort_field": "name", "sort_dir": "desc", "page_size": "5", **extra}
        return self.login(self.root).post(self.edit_url, data)

    def test_only_superusers_open_the_builder(self):
        self.assertEqual(self.login(self.user).get(reverse("core:search_builder")).status_code, 403)
        self.assertEqual(self.login(self.user).get(self.edit_url).status_code, 403)
        page = self.login(self.root).get(reverse("core:search_builder")).content.decode()
        self.assertIn("Garments", page)
        self.assertEqual(self.login(self.root).get(self.edit_url).status_code, 200)

    def test_standard_search_is_offered_and_paginated_for_everyone(self):
        r = self.save()
        self.assertEqual(r.status_code, 302)
        saved = SavedSearch.objects.get(name="Ready garments")
        self.assertTrue(saved.is_system)
        self.assertEqual((saved.sort, saved.page_size), (["-name"], 5))
        client = self.login(self.user)
        first = client.get(reverse("core:resource_list", args=["garments"]), {"saved": saved.pk}).content.decode()
        self.assertIn("G11", first)                                         # sorted by name, descending
        self.assertNotIn("G10", first)                                      # draft
        self.assertIn("Showing 1 to 5 of 6 entries", first)
        second = client.get(reverse("core:resource_list", args=["garments"]), {"saved": saved.pk, "page": 2}).content.decode()
        self.assertIn("G01", second)
        # a user can use it but not delete it
        client.post(reverse("core:search_action", args=[saved.pk, "delete"]))
        self.assertTrue(SavedSearch.objects.filter(pk=saved.pk).exists())

    def test_default_standard_search_applies_to_users_without_their_own_default(self):
        self.save(is_default="1")
        body = self.login(self.user).get(reverse("core:resource_list", args=["garments"])).content.decode()
        self.assertNotIn("G10", body)
        self.assertIn("G11", body)

    def test_editing_keeps_one_row(self):
        self.save()
        saved = SavedSearch.objects.get(name="Ready garments")
        r = self.login(self.root).post(self.edit_url, {"id": saved.pk, "name": "Ready (renamed)", "adv": json.dumps(READY)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SavedSearch.objects.filter(is_system=True).count(), 1)
        self.assertEqual(SavedSearch.objects.get(pk=saved.pk).name, "Ready (renamed)")
