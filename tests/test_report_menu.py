"""Saved reports in the sidebar menu (``menu_parent`` / ``menu_label`` / ``menu_position``), custom reports with ``menu=``, the stock
ledger custom report and the report builder's run-time date filter."""
import datetime as dt
import json
from decimal import Decimal

from django.contrib.auth.models import Group
from django.urls import reverse

from core.menu.service import menu_for
from core.models import MenuItem, QueryReport, ReportDefinition
from core.reports.builder.definition import ReportError, date_range, validate_definition
from core.reports.builder.run import build_queryset
from core.reports.registry import get_report_source, register_report_source, unregister_report_source
from core.templatetags.core_tags import is_active_path

from tests.dummyapp.models import Fabric, Garment, StockMove

from .base import CoreTestCase, make_user

VIEW = ["dummyapp.view_garment"]


def paths(user, company_id):
    """``{"Inventory > Yarn > Daily receive": url}`` for every link in the user's menu."""
    out = {}

    def walk(entries, prefix):
        for e in entries:
            title = f"{prefix} > {e.title}" if prefix else e.title
            if e.url:
                out[title] = e.url
            walk(e.children, title)

    walk(menu_for(user, company_id), "")
    return out


class MenuCase(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.inventory = MenuItem.objects.create(title="Inventory", icon="box", position=50)
        self.yarn = MenuItem.objects.create(title="Yarn", parent=self.inventory, position=1)
        self.dyeing = MenuItem.objects.create(title="Yarn dyeing", parent=self.inventory, position=2)
        self.owner = make_user("owner@example.com", self.company_a, perms=VIEW)
        self.friend = make_user("friend@example.com", self.company_a, perms=VIEW)
        self.stranger = make_user("stranger@example.com", self.company_a, perms=VIEW)

    def report(self, name="Daily receive", parent=None, **kw):
        kw.setdefault("owner", self.owner)
        return ReportDefinition.objects.create(name=name, source_key="garments", columns=["name"],
                                               menu_parent=parent if parent is not None else self.yarn, **kw)

    def menu(self, user):
        return paths(user, self.company_a.pk)


class ReportMenuVisibilityTests(MenuCase):
    def test_shown_under_its_group_for_the_owner_and_people_it_is_shared_with(self):
        rep = self.report()
        url = f"{reverse('core:report_builder', args=['garments'])}?def={rep.pk}"
        self.assertEqual(self.menu(self.owner).get("Inventory > Yarn > Daily receive"), url)
        self.assertNotIn("Inventory > Yarn > Daily receive", self.menu(self.friend))      # private: owner only
        rep.visibility = "users"
        rep.save()
        rep.shared_users.add(self.friend)                                                  # sharing clears the cached menus
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.friend))
        self.assertNotIn("Inventory > Yarn > Daily receive", self.menu(self.stranger))
        team = Group.objects.create(name="Store team")
        self.stranger.groups.add(team)
        rep.shared_groups.add(team)
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.stranger))
        rep.visibility, rep.is_system = "private", True                                    # standard: everyone with the source
        rep.save()
        rep.shared_users.clear()
        rep.shared_groups.clear()
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.friend))

    def test_source_permission_is_required_and_the_empty_group_is_dropped(self):
        self.report(visibility="public")
        nobody = make_user("noperm@example.com", self.company_a)
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.friend))
        titles = [e.title for e in menu_for(nobody, self.company_a.pk)]
        self.assertNotIn("Inventory", titles)                                              # nothing visible under it -> no group

    def test_private_report_name_never_reaches_other_users(self):
        self.report(name="Secret yarn count")
        home = reverse("core:dashboard")
        self.assertContains(self.login(self.owner).get(home), "Secret yarn count")
        page = self.login(self.friend).get(home)
        self.assertNotContains(page, "Secret yarn count")
        self.assertNotContains(page, ">Inventory<")

    def test_inactive_or_hidden_parent_hides_the_report(self):
        self.report(visibility="public")
        self.yarn.is_active = False
        self.yarn.save()
        self.assertNotIn("Inventory > Yarn > Daily receive", self.menu(self.owner))
        self.yarn.is_active = True
        self.yarn.save()
        self.inventory.is_active = False                                                   # an inactive group above it hides it too
        self.inventory.save()
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])
        self.inventory.is_active, self.inventory.superuser_only = True, True
        self.inventory.save()
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])

    def test_empty_group_is_dropped(self):
        self.assertNotIn("Inventory", [e.title for e in menu_for(self.owner, self.company_a.pk)])

    def test_deleting_the_report_or_the_group_leaves_no_dead_link(self):
        rep = self.report()
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.owner))
        rep.delete()
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])
        rep = self.report()
        self.yarn.delete()                                                                 # SET_NULL: the report stays, out of the menu
        rep.refresh_from_db()
        self.assertIsNone(rep.menu_parent_id)
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])

    def test_order_by_position_then_label_and_label_defaults_to_the_name(self):
        self.report("Stock report", menu_position=30)
        self.report("Daily issue", menu_position=20)
        self.report("Daily receive", menu_position=20)
        self.report("ledger-v2", menu_position=40, menu_label="Stock ledger")
        yarn = next(c for e in menu_for(self.owner, self.company_a.pk) if e.title == "Inventory" for c in e.children if c.title == "Yarn")
        self.assertEqual([c.title for c in yarn.children], ["Daily issue", "Daily receive", "Stock report", "Stock ledger"])

    def test_reports_merge_with_menu_items_by_position(self):
        MenuItem.objects.create(title="Yarn list", parent=self.yarn, path="/r/fabrics/", permission="dummyapp.view_garment", position=25)
        self.report("Daily issue", menu_position=20)
        self.report("Stock report", menu_position=30)
        yarn = next(c for e in menu_for(self.owner, self.company_a.pk) if e.title == "Inventory" for c in e.children if c.title == "Yarn")
        self.assertEqual([c.title for c in yarn.children], ["Daily issue", "Yarn list", "Stock report"])

    def test_menu_cache_is_cleared_when_a_report_changes(self):
        rep = self.report()
        self.assertIn("Inventory > Yarn > Daily receive", self.menu(self.owner))           # now cached
        rep.menu_parent = self.dyeing
        rep.save()
        menu = self.menu(self.owner)
        self.assertIn("Inventory > Yarn dyeing > Daily receive", menu)
        self.assertNotIn("Inventory > Yarn > Daily receive", menu)
        rep.name = "Daily receipt"
        rep.save()
        self.assertIn("Inventory > Yarn dyeing > Daily receipt", self.menu(self.owner))
        rep.menu_parent = None
        rep.save()
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])
        rep.menu_parent = self.yarn
        rep.save()
        self.assertIn("Inventory > Yarn > Daily receipt", self.menu(self.owner))
        rep.delete()
        self.assertEqual([k for k in self.menu(self.owner) if k.startswith("Inventory")], [])

    def test_four_levels_deep(self):
        reports = MenuItem.objects.create(title="Reports", parent=self.yarn)
        self.report(parent=reports)
        self.assertIn("Inventory > Yarn > Reports > Daily receive", self.menu(self.owner))
        html = self.login(self.owner).get(reverse("core:dashboard")).content.decode()
        self.assertIn("Daily receive", html)

    def test_saved_query_in_the_menu(self):
        q = QueryReport.objects.create(name="Yarn prices", root="dummyapp.garment", owner=self.owner, menu_parent=self.dyeing,
                                       definition={"columns": [{"path": "name"}]})
        self.assertEqual(self.menu(self.owner).get("Inventory > Yarn dyeing > Yarn prices"),
                         f"{reverse('core:query_builder', args=['dummyapp.garment'])}?q={q.pk}")
        self.assertNotIn("Inventory > Yarn dyeing > Yarn prices", self.menu(self.friend))

    def test_report_of_an_unknown_source_is_skipped(self):
        ReportDefinition.objects.create(name="Gone", source_key="no-such-source", owner=self.owner, menu_parent=self.yarn)
        self.assertNotIn("Inventory > Yarn > Gone", self.menu(self.owner))

    def test_menu_link_with_query_string_is_highlighted_only_for_that_report(self):
        rep = self.report()
        entry = next(c for e in menu_for(self.owner, self.company_a.pk) if e.title == "Inventory" for y in e.children for c in y.children)
        self.assertTrue(is_active_path(entry, entry.url))
        self.assertTrue(is_active_path(entry, f"{entry.url}&page=2"))
        self.assertFalse(is_active_path(entry, entry.url.replace(f"def={rep.pk}", f"def={rep.pk}1")))


class SaveFormTests(MenuCase):
    def setUp(self):
        super().setUp()
        self.builder = reverse("core:report_builder", args=["garments"])
        self.definition = json.dumps({"columns": ["name"]})

    def save(self, client, **extra):
        return client.post(self.builder, {"action": "save", "name": "Daily receive", "definition": self.definition, **extra})

    def test_valid_group_label_and_position_are_saved(self):
        client = self.login(self.owner)
        self.assertContains(client.get(self.builder), "Inventory &gt; Yarn dyeing")          # the picker lists the groups as a tree
        self.save(client, menu_parent=self.yarn.pk, menu_label="Receive", menu_position="5")
        rep = ReportDefinition.objects.get(owner=self.owner)
        self.assertEqual((rep.menu_parent_id, rep.menu_label, rep.menu_position), (self.yarn.pk, "Receive", 5))
        self.assertIn("Inventory > Yarn > Receive", self.menu(self.owner))
        client.post(f"{self.builder}?def={rep.pk}", {"action": "save", "name": "Daily receive", "definition": self.definition,
                                                     "menu_parent": "", "menu_label": "", "menu_position": "100"})
        rep.refresh_from_db()
        self.assertIsNone(rep.menu_parent_id)

    def test_server_rejects_links_inactive_hidden_and_unknown_groups(self):
        link = MenuItem.objects.create(title="Fabrics", parent=self.inventory, path="/r/fabrics/", permission="dummyapp.view_garment")
        off = MenuItem.objects.create(title="Old", parent=self.inventory, is_active=False)
        admin_only = MenuItem.objects.create(title="Admin", superuser_only=True)
        client = self.login(self.owner)
        for bad in (link.pk, off.pk, admin_only.pk, 999999, "x"):
            r = self.save(client, menu_parent=bad)
            self.assertEqual(r.status_code, 302, bad)
            self.assertFalse(ReportDefinition.objects.filter(owner=self.owner).exists(), bad)
        r = self.save(client, menu_parent=self.yarn.pk, menu_position="-3")
        self.assertFalse(ReportDefinition.objects.filter(owner=self.owner).exists())
        page = client.get(r.url)
        self.assertContains(page, "whole number")

    def test_only_people_who_may_change_the_report_get_the_picker(self):
        rep = self.report(visibility="public")
        self.assertContains(self.login(self.owner).get(f"{self.builder}?def={rep.pk}"), 'name="menu_parent"')
        self.assertNotContains(self.login(self.friend).get(f"{self.builder}?def={rep.pk}"), 'name="menu_parent"')
        self.login(self.friend).post(f"{self.builder}?def={rep.pk}", {"action": "save", "name": "Mine", "definition": self.definition})
        copy = ReportDefinition.objects.get(owner=self.friend)
        self.assertIsNone(copy.menu_parent_id)                                              # a copy starts out of the menu
        rep.refresh_from_db()
        self.assertEqual(rep.menu_parent_id, self.yarn.pk)

    def test_clone_is_not_in_the_menu(self):
        rep = self.report(visibility="public")
        self.login(self.friend).post(reverse("core:report_definition_action", args=[rep.pk, "clone"]))
        self.assertIsNone(ReportDefinition.objects.get(owner=self.friend).menu_parent_id)

    def test_query_builder_save_form(self):
        url = reverse("core:query_builder", args=["dummyapp.garment"])
        client = self.login(self.owner)
        definition = json.dumps({"columns": [{"path": "name"}]})
        client.post(url, {"definition": definition, "action": "save", "name": "Q", "visibility": "private", "menu_parent": self.inventory.pk + 9999})
        self.assertFalse(QueryReport.objects.exists())
        client.post(url, {"definition": definition, "action": "save", "name": "Q", "visibility": "private", "menu_parent": self.dyeing.pk})
        self.assertEqual(QueryReport.objects.get().menu_parent_id, self.dyeing.pk)
        self.assertIn("Inventory > Yarn dyeing > Q", self.menu(self.owner))

    def test_reports_page_marks_reports_in_the_menu(self):
        self.report()
        self.assertContains(self.login(self.owner).get(reverse("core:report_list")), "In the menu under Inventory &gt; Yarn")

    def test_menu_builder_accepts_an_empty_group(self):
        admin = make_user("admin@example.com", self.company_a, superuser=True)
        self.login(admin).post(reverse("core:menu_new"), {"title": "Knitting", "position": 0, "is_active": "on"})
        self.assertTrue(MenuItem.objects.filter(title="Knitting", parent=None).exists())


class CustomReportMenuAndLedgerTests(MenuCase):
    def setUp(self):
        super().setUp()
        self.fabric_group = MenuItem.objects.create(title="Fabric", parent=self.inventory)
        self.keeper = make_user("keeper@example.com", self.company_a, perms=["dummyapp.view_stockmove"])
        self.cotton = Fabric.objects.create(company=self.company_a, name="Cotton 30s")
        self.poly = Fabric.objects.create(company=self.company_a, name="Poly 40s")
        for day, qty_in, qty_out in ((dt.date(2026, 1, 5), 100, 0), (dt.date(2026, 2, 3), 0, 30), (dt.date(2026, 2, 10), 20, 0),
                                     (dt.date(2026, 3, 1), 0, 5)):
            StockMove.objects.create(company=self.company_a, fabric=self.cotton, moved_on=day, ref=f"C-{day.isoformat()}",
                                     qty_in=qty_in, qty_out=qty_out)
        StockMove.objects.create(company=self.company_a, fabric=self.poly, moved_on=dt.date(2026, 2, 4), qty_in=7)
        other = Fabric.objects.create(company=self.company_b, name="Foreign")
        StockMove.objects.create(company=self.company_b, fabric=other, moved_on=dt.date(2026, 2, 4), qty_in=999)

    def test_custom_report_shows_under_its_group_for_permission_holders(self):
        url = reverse("core:custom_report", args=["fabric-ledger"])
        self.assertEqual(paths(self.keeper, self.company_a.pk).get("Inventory > Fabric > Stock ledger"), url)
        self.assertNotIn("Inventory > Fabric > Stock ledger", self.menu(self.owner))           # no view_stockmove
        self.fabric_group.title = "Fabrics"                                                   # the code names "Inventory > Fabric"
        self.fabric_group.save()
        self.assertNotIn("Inventory > Fabrics > Stock ledger", paths(self.keeper, self.company_a.pk))

    def test_ledger_opening_running_and_closing_balance(self):
        client = self.login(self.keeper)
        r = client.get(reverse("core:custom_report", args=["fabric-ledger"]),
                       {"run": "1", "item": self.cotton.pk, "date_from": "2026-02-01", "date_to": "2026-02-28"})
        rows = r.context["output"].rows
        self.assertEqual([row.get("particulars") or row.get("ref") for row in rows],
                         ["Opening balance", "C-2026-02-03", "C-2026-02-10", "Closing balance"])
        self.assertEqual([row["balance"] for row in rows], [Decimal(100), Decimal(70), Decimal(90), Decimal(90)])
        self.assertEqual((rows[-1]["received"], rows[-1]["issued"]), (Decimal(20), Decimal(30)))

    def test_ledger_every_item_and_company_scope(self):
        client = self.login(self.keeper)
        r = client.get(reverse("core:custom_report", args=["fabric-ledger"]), {"run": "1", "date_from": "2026-02-01", "date_to": "2026-02-28"})
        rows = r.context["output"].rows
        self.assertEqual({row["item"] for row in rows}, {"Cotton 30s", "Poly 40s"})          # never the other company's fabric
        closing = {row["item"]: row["balance"] for row in rows if row.get("particulars") == "Closing balance"}
        self.assertEqual(closing, {"Cotton 30s": Decimal(90), "Poly 40s": Decimal(7)})
        form = r.context["form"]
        self.assertEqual(set(form.fields["item"].queryset), {self.cotton, self.poly})        # the item picker is scoped too
        bad = client.get(reverse("core:custom_report", args=["fabric-ledger"]), {"run": "1", "date_from": "2026-03-01", "date_to": "2026-02-01"})
        self.assertNotIn("output", bad.context)


class DateFilterTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        register_report_source("t-dated-garments", Garment, label="Dated garments", columns=["name", "ordered_on"],
                               date_field="ordered_on", permission="dummyapp.view_garment")
        self.source = get_report_source("t-dated-garments")
        self.user = make_user("d@example.com", self.company_a, perms=VIEW)
        today = dt.date.today()
        for name, day in (("today", today), ("yesterday", today - dt.timedelta(days=1)), ("old", today - dt.timedelta(days=90))):
            Garment.objects.create(company=self.company_a, name=name, style_no=name, ordered_on=day)

    def tearDown(self):
        unregister_report_source("t-dated-garments")
        super().tearDown()

    def names(self, parameters):
        from core.crud.access import Ctx

        ctx = Ctx(user=self.user, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk, member_ids=frozenset({self.company_a.pk}))
        spec = validate_definition(self.source, {"columns": ["name"], "parameters": parameters})
        return sorted(r["name"] for r in build_queryset(self.source, spec, ctx).queryset)

    def test_presets_are_resolved_on_the_day_the_report_runs(self):
        self.assertEqual(self.names({"date_preset": "today"}), ["today"])
        self.assertEqual(self.names({"date_preset": "yesterday"}), ["yesterday"])
        day = (dt.date.today() - dt.timedelta(days=90)).isoformat()
        self.assertEqual(self.names({"date_preset": "day", "date_from": day}), ["old"])
        self.assertEqual(self.names({"date_from": day}), ["old", "today", "yesterday"])
        self.assertEqual(self.names({}), ["old", "today", "yesterday"])

    def test_date_range_helper(self):
        d = dt.date(2026, 3, 18)                                                              # a Wednesday
        self.assertEqual(date_range({"date_preset": "this_week"}, d), (dt.date(2026, 3, 16), d))
        self.assertEqual(date_range({"date_preset": "this_month"}, d), (dt.date(2026, 3, 1), d))
        self.assertEqual(date_range({"date_preset": "last_month"}, d), (dt.date(2026, 2, 1), dt.date(2026, 2, 28)))
        self.assertEqual(date_range({"date_from": "2026-01-01", "date_to": ""}, d), (dt.date(2026, 1, 1), None))

    def test_bad_date_choices_are_refused(self):
        for bad in ({"date_preset": "tomorrow"}, {"date_preset": "day"}, {"date_from": "2026-03-02", "date_to": "2026-03-01"}):
            with self.assertRaises(ReportError, msg=str(bad)):
                validate_definition(self.source, {"columns": ["name"], "parameters": bad})
        with self.assertRaises(ReportError):                                                  # a source without a date field
            validate_definition(get_report_source("garments"), {"columns": ["name"], "parameters": {"date_preset": "today"}})

    def test_run_page_offers_the_date_filter_and_a_saved_daily_report_uses_it(self):
        client = self.login(self.user)
        url = reverse("core:report_builder", args=["t-dated-garments"])
        self.assertContains(client.get(url), 'x-model="d.parameters.date_preset"')
        self.assertNotContains(client.get(reverse("core:report_builder", args=["garments"])), "d.parameters.date_preset")
        client.post(url, {"action": "save", "name": "Daily", "definition": json.dumps({"columns": ["name"], "parameters": {"date_preset": "today"}})})
        rep = ReportDefinition.objects.get(name="Daily")
        self.assertEqual(rep.parameters["date_preset"], "today")
        page = client.get(f"{url}?def={rep.pk}")
        self.assertContains(page, "<td>today</td>", html=False)
        self.assertNotContains(page, "<td>old</td>")
        r = client.get(url, {"definition": json.dumps({"columns": ["name"], "parameters": {"date_preset": "yesterday"}})},   # changed per run
                       HTTP_HX_REQUEST="true", HTTP_HX_TARGET="report-preview")
        self.assertContains(r, "<td>yesterday</td>")
        self.assertNotContains(r, "<td>today</td>")
