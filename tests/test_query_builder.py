import csv
import datetime as dt
import io
import json

from django.urls import reverse

from core.crud.access import Ctx
from core.models import QueryReport
from core.reports.query import QueryError, build, catalogue, get_query_model, preview, validate

from tests.dummyapp.models import Fabric, Garment, GarmentLine

from .base import CoreTestCase, make_user

PERMS = ["dummyapp.view_garment", "dummyapp.view_fabric", "dummyapp.view_garmentline"]


class QueryBuilderTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("qb@example.com", self.company_a, perms=PERMS)
        self.ctx = Ctx(user=self.user, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk,
                       member_ids=frozenset({self.company_a.pk}))
        self.root = get_query_model("dummyapp.garment")
        cotton = Fabric.objects.create(company=self.company_a, name="Cotton")
        self.foreign_fabric = Fabric.objects.create(company=self.company_b, name="Foreign silk")
        self.g1 = Garment.objects.create(company=self.company_a, name="Polo", style_no="P-1", price=10, status="draft", fabric=cotton,
                                         ordered_on=dt.date(2026, 1, 15))
        self.g2 = Garment.objects.create(company=self.company_a, name="Tee", style_no="T-1", price=20, status="ready", fabric=cotton,
                                         ordered_on=dt.date(2026, 2, 3))
        self.g3 = Garment.objects.create(company=self.company_a, name="Cap", style_no="C-1,BLUE", price=5, status="ready",
                                         fabric=self.foreign_fabric, ordered_on=dt.date(2026, 2, 20))
        Garment.objects.create(company=self.company_b, name="Other company", style_no="X", price=99, status="ready")
        for g, qtys in ((self.g1, (5, 7)), (self.g2, (1,)), (self.g3, (4,))):
            for n, qty in enumerate(qtys):
                GarmentLine.objects.create(garment=g, description=f"{g.name} line {n}", qty=qty, position=n)

    def run_query(self, definition):
        spec = validate(self.root, self.user, definition)
        return build(spec, self.ctx).rows()

    # ----------------------------------------------------------------------------------------------------------- catalogue
    def test_catalogue_walks_relations_and_hides_secrets(self):
        paths = {f["p"] for f in catalogue(self.root, self.user)}
        self.assertIn("name", paths)
        self.assertIn("fabric__name", paths)                    # forward join
        self.assertIn("lines__qty", paths)                      # reverse join (registered with register_query_model)
        self.assertNotIn("secret_note", paths)                  # secret-looking names are never offered
        self.assertFalse(any(p.startswith("lines__garment__") for p in paths))     # never straight back over the same relation

    def test_joins_need_the_joined_models_view_permission(self):
        limited = make_user("limited@example.com", self.company_a, perms=["dummyapp.view_garment"])
        paths = {f["p"] for f in catalogue(self.root, limited)}
        self.assertIn("name", paths)
        self.assertNotIn("fabric__name", paths)
        with self.assertRaises(QueryError):
            validate(self.root, limited, {"columns": [{"path": "fabric__name"}]})

    def test_unknown_paths_functions_and_operators_are_refused(self):
        for bad in ({"columns": [{"path": "company__name__password"}]}, {"columns": [{"path": "secret_note"}]},
                    {"columns": [{"path": "name", "fn": "hour"}]}, {"columns": [{"path": "name", "agg": "sum"}]},
                    {"columns": [{"path": "name"}], "filters": {"op": "AND", "items": [{"path": "name", "op": "regex", "value": "x"}]}},
                    {"columns": [{"path": "name"}], "sort": [{"alias": "nope"}]}, {"columns": []}):
            with self.assertRaises(QueryError, msg=bad):
                validate(self.root, self.user, bad)

    # ------------------------------------------------------------------------------------------------------- query shapes
    def test_columns_filters_and_company_scope(self):
        rows = self.run_query({"columns": [{"path": "name"}, {"path": "fabric__name", "alias": "fabric"}],
                               "filters": {"op": "AND", "items": [{"path": "price", "op": "between", "value": [5, 20]},
                                                                  {"path": "status", "op": "ne", "value": "draft"},
                                                                  {"path": "name", "op": "in", "value": "Tee, Cap, Other company"}]},
                               "sort": [{"alias": "name", "dir": "asc"}]})
        # Cap is joined to a fabric of ANOTHER company: the join scope removes that row; company B garments never appear
        self.assertEqual(rows, [{"name": "Tee", "fabric": "Cotton"}])

    def test_related_record_columns_show_names_not_ids(self):
        rows = self.run_query({"columns": [{"path": "fabric", "alias": "fabric"}, {"agg": "count", "alias": "n"}],
                               "sort": [{"alias": "n", "dir": "desc"}]})
        # Cotton is visible -> its name; the other company's fabric is not -> only its id, its name never leaks
        self.assertEqual(rows, [{"fabric": "Cotton", "n": 2}, {"fabric": self.foreign_fabric.pk, "n": 1}])

    def test_join_scope_keeps_rows_without_a_related_row(self):
        Garment.objects.create(company=self.company_a, name="Bare", style_no="B", price=1)
        rows = self.run_query({"columns": [{"path": "name"}, {"path": "fabric__name"}],
                               "filters": {"op": "AND", "items": [{"path": "name", "op": "eq", "value": "Bare"}]}})
        self.assertEqual(rows, [{"name": "Bare", "fabric_name": None}])

    def test_inner_join_keeps_only_rows_with_a_match_left_keeps_all(self):
        Garment.objects.create(company=self.company_a, name="Bare", style_no="B", price=1)          # no fabric, no lines
        names = lambda join_type: sorted(r["name"] for r in self.run_query(  # noqa: E731
            {"joins": [{"path": "fabric", "type": join_type}], "columns": [{"path": "name"}, {"path": "fabric__name"}]}))
        self.assertIn("Bare", names("left"))
        self.assertNotIn("Bare", names("inner"))
        inner_lines = self.run_query({"joins": [{"path": "lines", "type": "inner"}], "columns": [{"path": "name"}], "distinct": True})
        self.assertNotIn("Bare", [r["name"] for r in inner_lines])

    def test_fields_of_a_table_that_is_not_joined_are_refused(self):
        with self.assertRaisesMessage(QueryError, "Joins tab"):
            validate(self.root, self.user, {"joins": [], "columns": [{"path": "name"}, {"path": "fabric__name"}]})
        with self.assertRaises(QueryError):
            validate(self.root, self.user, {"joins": [{"path": "nothing"}], "columns": [{"path": "name"}]})
        spec = validate(self.root, self.user, {"columns": [{"path": "lines__qty"}]})                 # older definition: derived
        self.assertEqual(spec.joins, [{"path": "lines", "type": "left"}])
        from core.reports.query import to_json

        self.assertEqual(to_json(spec)["joins"], [{"path": "lines", "type": "left"}])

    def test_soft_deleted_joined_rows_are_left_out(self):
        self.g1.fabric.delete()                                     # soft delete
        rows = self.run_query({"columns": [{"path": "name"}, {"path": "fabric__name"}],
                               "filters": {"op": "AND", "items": [{"path": "name", "op": "eq", "value": "Polo"}]}})
        self.assertEqual(rows, [])

    def test_group_by_with_aggregates_and_having(self):
        rows = self.run_query({"columns": [{"path": "status"}, {"agg": "count", "alias": "rows"},
                                           {"path": "price", "agg": "sum", "alias": "total"}],
                               "having": [{"alias": "rows", "op": "gt", "value": 1}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["rows"], float(rows[0]["total"])), ("ready", 2, 25.0))

    def test_join_to_many_with_conditional_aggregates(self):
        """WHERE on the joined rows + MAX(CASE WHEN ...) style "only where" aggregates, grouped per garment."""
        rows = self.run_query({"columns": [{"path": "name"}, {"path": "lines__qty", "agg": "sum", "alias": "qty"},
                                           {"path": "lines__qty", "agg": "max", "alias": "big", "where": {"path": "lines__qty", "op": "gte", "value": 5}},
                                           {"agg": "count", "alias": "lines"}],
                               "filters": {"op": "AND", "items": [{"path": "lines__qty", "op": "gt", "value": 1}]},
                               "sort": [{"alias": "name", "dir": "asc"}]})
        self.assertEqual(rows, [{"name": "Cap", "qty": 4, "big": None, "lines": 1},          # Tee's only line (qty 1) is filtered out
                                {"name": "Polo", "qty": 12, "big": 7, "lines": 2}])

    def test_functions_case_and_date_parts(self):
        rows = self.run_query({"columns": [{"path": "ordered_on", "fn": "month", "alias": "month"},
                                           {"case": {"when": {"path": "status", "op": "in", "value": ["ready"]}, "then": 2, "else": 1},
                                            "agg": "max", "alias": "kind"},
                                           {"agg": "count", "alias": "n"}],
                               "filters": {"op": "AND", "items": [{"path": "fabric__name", "op": "eq", "value": "Cotton"}]},
                               "sort": [{"alias": "month", "dir": "desc"}]})
        self.assertEqual(rows, [{"month": 2, "kind": 2, "n": 1}, {"month": 1, "kind": 1, "n": 1}])

    def test_text_before_and_after_a_separator(self):
        self.g3.fabric = None
        self.g3.save()
        rows = self.run_query({"columns": [{"path": "style_no", "fn": "before", "arg": ",", "alias": "style"},
                                           {"path": "style_no", "fn": "after", "arg": ",", "alias": "colour"}],
                               "sort": [{"alias": "style", "dir": "asc"}]})
        self.assertEqual(rows, [{"style": "C-1", "colour": "BLUE"}, {"style": "P-1", "colour": None}, {"style": "T-1", "colour": None}])

    def test_date_filters_distinct_and_limit(self):
        rows = self.run_query({"columns": [{"path": "status"}], "distinct": True,
                               "filters": {"op": "OR", "items": [{"path": "ordered_on", "op": "gte", "value": "2026-02-01"},
                                                                 {"path": "ordered_on", "fn": "month", "op": "eq", "value": 1}]},
                               "sort": [{"alias": "status", "dir": "desc"}], "limit": 1})
        self.assertEqual(rows, [{"status": "ready"}])

    def test_preview_has_display_values_and_chart(self):
        spec = validate(self.root, self.user, {"columns": [{"path": "status"}, {"agg": "count", "alias": "n"}],
                                               "chart": {"type": "bar", "x": "status", "y": ["n"]}})
        result = preview(spec, self.ctx)
        self.assertEqual(result["chart"]["labels"], ["Draft", "Ready"])                  # choice labels, not raw values
        self.assertEqual(result["chart"]["datasets"][0]["data"], [1.0, 2.0])
        self.assertEqual(result["rows"][0][0], ("Draft", False))

    # -------------------------------------------------------------------------------------------------------------- views
    def test_pages_preview_save_and_export(self):
        client = self.login(self.user)
        self.assertContains(client.get(reverse("core:query_start")), "dummyapp.garment")
        url = reverse("core:query_builder", args=["dummyapp.garment"])
        self.assertEqual(client.get(url).status_code, 200)
        definition = json.dumps({"columns": [{"path": "name"}, {"path": "price", "alias": "price"}],
                                 "sort": [{"alias": "price", "dir": "desc"}]})
        r = client.post(url, {"definition": definition, "action": "preview"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "Tee")
        self.assertNotContains(r, "Other company")
        r = client.post(url, {"definition": definition, "action": "save", "name": "Prices", "visibility": "private"})
        report = QueryReport.objects.get(name="Prices")
        self.assertRedirects(r, f"{url}?q={report.pk}", fetch_redirect_response=False)
        self.assertContains(client.get(f"{url}?q={report.pk}"), "Tee")
        data = client.post(url, {"definition": definition, "action": "export", "format": "csv"}).content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(data)))
        self.assertEqual(rows[0], ["Name", "Price"])
        self.assertEqual(rows[1][0], "Tee")
        pdf = client.post(url, {"definition": definition, "action": "export", "format": "pdf"})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertContains(client.get(reverse("core:report_list")), "Prices")               # saved queries are listed on Reports too
        other = make_user("other@example.com", self.company_a, perms=PERMS)
        self.assertEqual(self.login(other).get(f"{url}?q={report.pk}").status_code, 404)        # private to its owner

    def test_chart_is_rendered_in_both_builders(self):
        client = self.login(self.user)
        definition = json.dumps({"columns": [{"path": "status"}, {"agg": "count", "alias": "n"}], "chart": {"type": "pie", "x": "status", "y": ["n"]}})
        r = client.post(reverse("core:query_builder", args=["dummyapp.garment"]), {"definition": definition, "action": "preview"},
                        HTTP_HX_REQUEST="true")
        self.assertContains(r, "data-chart=")
        self.assertContains(r, "&quot;type&quot;: &quot;pie&quot;")
        definition = json.dumps({"columns": ["status"], "groupings": ["status"], "aggregates": [{"fn": "count", "column": ""}],
                                 "chart": {"type": "bar", "x": "status", "y": ["count_rows"]}})
        r = client.get(reverse("core:report_builder", args=["garments"]), {"definition": definition}, HTTP_HX_REQUEST="true",
                       HTTP_HX_TARGET="report-preview")
        self.assertContains(r, "data-chart=")
        self.assertContains(r, "Showing 1 to 2 of 2 entries")

    def test_invalid_definition_is_a_message_not_a_crash(self):
        url = reverse("core:query_builder", args=["dummyapp.garment"])
        r = self.login(self.user).post(url, {"definition": json.dumps({"columns": [{"path": "nope"}]}), "action": "preview"},
                                       HTTP_HX_REQUEST="true")
        self.assertContains(r, "Unknown or forbidden field")

    def test_root_needs_permission(self):
        nobody = make_user("nobody@example.com", self.company_a)
        client = self.login(nobody)
        self.assertEqual(client.get(reverse("core:query_builder", args=["dummyapp.garment"])).status_code, 403)
        self.assertNotContains(client.get(reverse("core:query_start")), "dummyapp.garment")
        self.assertEqual(client.get(reverse("core:query_builder", args=["auth.permission"])).status_code, 404)
