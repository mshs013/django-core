"""List columns from related records (``profile__dept__name``), renamed with ``register_resource(labels=...)`` and sortable: the
Users page shows Department / Designation / Company (abbreviation)."""
import re

from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from core.crud.registry import get_resource, register_resource, unregister_resource
from core.models import Department, Designation

from tests.dummyapp.models import Fabric

from .base import CoreTestCase, make_user


class UserListColumnTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.company_a.name, self.company_a.abbreviation = "Alpha Knit Limited", "AKL"
        self.company_a.save()
        self.root = make_user("root@example.com", self.company_a, superuser=True)
        for email, dept, desig in (("zed@example.com", "Accounts", "Officer"), ("amy@example.com", "Sewing", "Manager")):
            user = make_user(email, self.company_a)
            user.profile.dept = Department.objects.create(name=dept)
            user.profile.designation = Designation.objects.create(name=desig)
            user.profile.save()

    def page(self, **params):
        return self.login(self.root).get(reverse("core:resource_list", args=["users"]), params).content.decode()

    def headers(self, html):
        head = html.split("<thead", 1)[1].split("</thead>", 1)[0]
        return [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]

    def test_headings_and_company_abbreviation(self):
        html = self.page()
        headers = self.headers(html)
        for label in ("Department", "Designation", "Company"):
            self.assertIn(label, headers)
        self.assertNotIn("Default company name", headers)
        body = html.split("<tbody", 1)[1]
        for value in ("AKL", "Accounts", "Officer", "Sewing", "Manager"):     # the cells, not only the top-bar company switcher
            self.assertIn(value, body)
        self.assertNotIn("Alpha Knit Limited", body)
        self.assertNotIn(">None<", body)

    def test_new_columns_are_sortable(self):
        html = self.page()
        for path in ("profile__dept__name", "profile__designation__name", "profile__default_company__abbreviation"):
            self.assertIn(f"sort={path}", html)
        by_dept = self.page(sort="profile__dept__name")
        self.assertLess(by_dept.index("zed@example.com"), by_dept.index("amy@example.com"))       # Accounts before Sewing
        by_desig = self.page(sort="-profile__designation__name")
        self.assertLess(by_desig.index("zed@example.com"), by_desig.index("amy@example.com"))     # Officer before Manager, descending

    def test_labels_reach_the_list_pdf_headings(self):
        self.assertEqual(get_resource("users").field_label("profile__default_company__abbreviation"), "Company")


class RelatedColumnRegistrationTests(CoreTestCase):
    def tearDown(self):
        unregister_resource("fabric-paths")

    def test_bad_path_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            register_resource(Fabric, slug="fabric-paths", list_fields=("name", "company__nonsense"))

    def test_unlabelled_path_uses_the_last_field_name(self):
        cfg = register_resource(Fabric, slug="fabric-paths", list_fields=("name", "company__abbreviation"))
        self.assertEqual(cfg.field_label("company__abbreviation"), "Abbreviation")


class EmptyValueTests(CoreTestCase):
    """Missing values show an em dash everywhere (list, detail, inline tables), never "None" or "{}"; 0 stays 0."""

    def test_dash_filter(self):
        from core.templatetags.core_tags import dash

        self.assertEqual([dash(v) for v in (None, "", 0, False, "x")], ["—", "—", 0, False, "x"])

    def test_approval_type_detail_inline_table(self):
        from core.models import ApprovalStep, ApprovalType, ApprovalTypeStep

        atype = ApprovalType.objects.create(code="po", name="PO approval")
        ApprovalTypeStep.objects.create(approval_type=atype, step=ApprovalStep.objects.create(code="ok", name="Approve"), position=0)
        root = make_user("root@example.com", self.company_a, superuser=True)
        html = self.login(root).get(reverse("core:resource_detail", args=["approval-types", atype.pk])).content.decode()
        table = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertIn("<td>0</td>", table)
        self.assertNotIn(">None<", table)
        self.assertNotIn("{}", table)
