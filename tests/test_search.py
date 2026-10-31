import json
from datetime import date

from django.http import QueryDict

from core.crud.access import Ctx
from core.crud.registry import get_resource
from core.search.builder.state import apply_state, parse_state
from core.search.errors import SearchError
from core.search.registry import get_search

from tests.dummyapp.models import Fabric, Garment

from .base import CoreTestCase, make_user

BUILD = ["core.build_search"]
VIEW = ["dummyapp.view_garment"]


class SearchBuilderTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.spec = get_search("resource", "garments")
        f = Fabric.objects.create(company=self.company_a, name="Cotton")
        self.rows = [Garment.objects.create(company=self.company_a, name=n, style_no=s, price=p, status=st, fabric=f if n == "Tee" else None,
                                            ordered_on=date(2026, 1, d))
                     for n, s, p, st, d in (("Tee", "T1", 10, "draft", 1), ("Hoodie 100%", "H1", 30, "ready", 15), ("Polo_x", "P1", 20, "draft", 28))]

    def _run(self, **params):
        qd = QueryDict(mutable=True)
        for k, v in params.items():
            qd[k] = v if isinstance(v, str) else json.dumps(v)
        return list(apply_state(self.spec, Garment.objects.all(), parse_state(self.spec, qd, strict=True)).order_by("pk"))

    def test_quick_search_tokens_are_anded_and_wildcards_are_literal(self):
        self.assertEqual([g.name for g in self._run(q="tee")], ["Tee"])
        self.assertEqual([g.name for g in self._run(q="100%")], ["Hoodie 100%"])
        self.assertEqual([g.name for g in self._run(q="_")], ["Polo_x"])

    def test_and_or_and_nested_group(self):
        leaf = lambda f, op, v: {"field": f, "op": op, "value": v}
        r = self._run(adv={"op": "AND", "items": [leaf("status", "eq", "draft"), {"op": "OR", "items": [leaf("name", "icontains", "tee"), leaf("name", "icontains", "polo")]}]})
        self.assertEqual([g.name for g in r], ["Tee", "Polo_x"])

    def test_range_and_dates_and_isnull(self):
        leaf = lambda f, op, v: {"field": f, "op": op, "value": v}
        self.assertEqual(len(self._run(adv={"op": "AND", "items": [leaf("price", "range", ["15", "25"])]})), 1)
        self.assertEqual(len(self._run(adv={"op": "AND", "items": [leaf("ordered_on", "range", ["2026-01-10", ""])]})), 2)
        self.assertEqual(len(self._run(adv={"op": "AND", "items": [leaf("fabric", "is_null", True)]})), 2)

    def test_rejects_unknown_fields_operators_and_deep_nesting(self):
        for bad in ({"op": "AND", "items": [{"field": "secret_note", "op": "eq", "value": "x"}]},
                    {"op": "AND", "items": [{"field": "price", "op": "regex", "value": ".*"}]},
                    {"op": "AND", "items": [{"op": "OR", "items": [{"op": "AND", "items": [{"field": "name", "op": "eq", "value": "x"}]}]}]},
                    {"op": "DROP", "items": []}):
            with self.assertRaises(SearchError):
                self._run(adv=bad)

    def test_bad_sort_and_page_size_are_bounded(self):
        with self.assertRaises(SearchError):
            self._run(sort="secret_note")
        qd = QueryDict("page_size=999999")
        self.assertLessEqual(parse_state(self.spec, qd).page_size, 100)

    def test_multivalued_filters_do_not_duplicate_rows(self):
        from tests.dummyapp.models import GarmentLine

        g = self.rows[0]
        for i in range(3):
            GarmentLine.objects.create(garment=g, description="same", qty=1, position=i)
        self.assertEqual(len([r for r in self._run(q="Tee")]), 1)

    def test_saved_search_roundtrip_visibility_and_revalidation(self):
        from core.models import SavedSearch
        from core.search import service

        alice, bob = make_user("al@example.com", self.company_a, perms=BUILD), make_user("bo@example.com", self.company_a)
        state = parse_state(self.spec, QueryDict("q=tee"))
        saved = service.save_search(alice, self.company_a.pk, target_type="resource", key="garments", name="Tees", state=state)
        self.assertEqual(len(service.usable_searches(alice, "resource", "garments", self.company_a.pk)), 1)
        self.assertEqual(len(service.usable_searches(bob, "resource", "garments", self.company_a.pk)), 0)
        service.share(alice, saved, "company")
        self.assertEqual(len(service.usable_searches(bob, "resource", "garments", self.company_a.pk)), 1)
        SavedSearch.objects.filter(pk=saved.pk).update(definition={"op": "AND", "items": [{"field": "gone", "op": "eq", "value": 1}]})
        self.assertIsNone(service.validated_state(SavedSearch.objects.get(pk=saved.pk)))

    def test_one_default_per_owner_target(self):
        from core.search import service

        alice = make_user("d1@example.com", self.company_a, perms=BUILD)
        state = parse_state(self.spec, QueryDict(""))
        a = service.save_search(alice, self.company_a.pk, target_type="resource", key="garments", name="A", state=state, is_default=True)
        b = service.save_search(alice, self.company_a.pk, target_type="resource", key="garments", name="B", state=state, is_default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertTrue(b.is_default)


class SearchRolesTests(CoreTestCase):
    """Superusers (and holders of core.build_search) build searches; everyone else only uses the ones offered to them."""

    def setUp(self):
        super().setUp()
        from django.urls import reverse

        self.list_url = reverse("core:resource_list", args=["garments"])
        self.spec = get_search("resource", "garments")
        self.admin = make_user("root@example.com", self.company_a, superuser=True)
        self.user = make_user("viewer@example.com", self.company_a, perms=VIEW)
        self.other_co_user = make_user("viewer-b@example.com", self.company_b, perms=VIEW)
        Garment.objects.create(company=self.company_a, name="Tee", style_no="T1", status="draft")
        Garment.objects.create(company=self.company_a, name="Hoodie", style_no="H1", status="ready")
        Garment.objects.create(company=self.company_b, name="Foreign ready", style_no="F1", status="ready")
        self.ready_tree = {"op": "AND", "items": [{"field": "status", "op": "eq", "value": "ready"}]}

    def _save_system(self):
        from core.search import service

        state = parse_state(self.spec, QueryDict(mutable=True))
        state.tree = self.ready_tree
        return service.save_search(self.admin, self.company_a.pk, target_type="resource", key="garments", name="Ready", state=state,
                                   is_system=True)

    def test_plain_user_cannot_save_or_preview(self):
        client = self.login(self.user)
        post = {"target_type": "resource", "target_key": "garments", "name": "Mine", "adv": json.dumps(self.ready_tree)}
        self.assertEqual(client.post(self._url("core:search_save"), post).status_code, 403)
        self.assertEqual(client.post(self._url("core:search_preview"), post).status_code, 403)
        from core.models import SavedSearch

        self.assertFalse(SavedSearch.objects.exists())

    def test_plain_user_hand_made_adv_url_is_ignored_and_builder_hidden(self):
        body = self.login(self.user).get(self.list_url, {"adv": json.dumps(self.ready_tree)}).content.decode()
        self.assertIn("Tee", body)                                   # filter ignored -> draft row still listed
        self.assertNotIn("+ Filter", body)
        self.assertNotIn("Save this search as", body)

    def test_builder_permission_and_superuser_see_the_builder(self):
        holder = make_user("builder@example.com", self.company_a, perms=[*VIEW, *BUILD])
        for who in (holder, self.admin):
            body = self.login(who).get(self.list_url).content.decode()
            self.assertIn("+ Filter", body)
            self.assertIn("Save this search as", body)
        self.assertIn("Standard (all users)", self.login(self.admin).get(self.list_url).content.decode())
        self.assertNotIn("Standard (all users)", self.login(holder).get(self.list_url).content.decode())

    def test_system_search_is_offered_to_users_in_every_company_and_stays_company_scoped(self):
        saved = self._save_system()
        body = self.login(self.user).get(self.list_url, {"saved": saved.pk}).content.decode()
        self.assertIn("Hoodie", body)
        self.assertNotIn("Tee", body)
        self.assertIn("Standard", body)
        body_b = self.login(self.other_co_user).get(self.list_url, {"saved": saved.pk}).content.decode()
        self.assertIn("Foreign ready", body_b)                       # offered in company B, rows still B only
        self.assertNotIn("Hoodie", body_b)

    def test_quick_text_narrows_an_offered_search(self):
        saved = self._save_system()
        Garment.objects.create(company=self.company_a, name="Polo", style_no="P1", status="ready")
        body = self.login(self.user).get(self.list_url, {"saved": saved.pk, "q": "polo"}).content.decode()
        self.assertIn("Polo", body)
        self.assertNotIn("Hoodie", body)

    def test_only_superusers_manage_system_searches(self):
        from core.search import service

        saved = self._save_system()
        holder = make_user("builder2@example.com", self.company_a, perms=[*VIEW, *BUILD])
        state = parse_state(self.spec, QueryDict(""))
        with self.assertRaises(SearchError):
            service.save_search(holder, self.company_a.pk, target_type="resource", key="garments", name="Sys", state=state, is_system=True)
        for action in (lambda: service.delete_search(holder, saved), lambda: service.share(holder, saved, "private"),
                       lambda: service.delete_search(self.user, saved)):
            with self.assertRaises(SearchError):
                action()
        r = self.login(self.user).post(self._url("core:search_action", saved.pk, "delete"))
        self.assertEqual(r.status_code, 302)                         # refused with a message, row kept
        self.assertTrue(type(saved).objects.filter(pk=saved.pk).exists())
        service.delete_search(self.admin, saved)
        self.assertFalse(type(saved).objects.filter(pk=saved.pk).exists())

    def test_pinned_system_search_appears_in_menu_only_for_users_who_can_view(self):
        from core.menu.service import invalidate_menus, menu_for

        saved = self._save_system()
        type(saved).objects.filter(pk=saved.pk).update(pinned_to_menu=True)
        invalidate_menus()
        names = lambda u: [c.title for e in menu_for(u, u.profile.active_company_id) for c in getattr(e, "children", [])]
        self.assertIn("Ready", names(self.user))
        nobody = make_user("noview@example.com", self.company_a)
        invalidate_menus()
        self.assertNotIn("Ready", names(nobody))

    def _url(self, name, *args):
        from django.urls import reverse

        return reverse(name, args=args)
