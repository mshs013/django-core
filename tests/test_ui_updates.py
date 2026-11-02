"""Company edit with a logo, readable audit rows, site-wide date format, pager,
company switcher and the plain (unlinked) first column."""
import datetime as dt
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from core.audit.display import change_rows, verbose_model_name
from core.formatting import format_value
from core.models import AuditLog

from tests.dummyapp.models import Fabric, Garment

from .base import CoreTestCase, make_company, make_user

PERMS = ["dummyapp.view_garment", "dummyapp.change_garment", "dummyapp.view_fabric"]


def png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), "image/png")


class CompanyEditTests(CoreTestCase):
    def test_company_with_a_logo_can_be_edited(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        self.company_a.logo.save("logo.png", png())
        client = self.login(admin)
        url = reverse("core:resource_edit", args=["companies", self.company_a.pk])
        r = client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)                    # private files have no URL: must not crash
        self.assertContains(r, "Currently")
        r = client.post(url, {"name": "Renamed", "abbreviation": "A", "address": "x", "is_active": "on"}, HTTP_HX_REQUEST="true")
        self.assertIn(r.status_code, (200, 204, 302))
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, "Renamed")
        self.assertTrue(self.company_a.logo)                    # an untouched logo is kept

    def test_logo_is_served_to_members_only(self):
        self.company_a.logo.save("logo.png", png())
        member = make_user("m@example.com", self.company_a)
        outsider = make_user("o@example.com", self.company_b)
        url = reverse("core:company_logo", args=[self.company_a.pk])
        self.assertEqual(self.login(member).get(url).status_code, 200)
        self.assertEqual(self.login(outsider).get(url).status_code, 404)


class AuditDisplayTests(CoreTestCase):
    def test_title_and_changes_are_readable(self):
        cotton = Fabric.objects.create(company=self.company_a, name="Cotton")
        silk = Fabric.objects.create(company=self.company_a, name="Silk")
        garment = Garment.objects.create(company=self.company_a, name="Polo", style_no="P", fabric=cotton, ordered_on=dt.date(2026, 1, 2))
        garment.fabric, garment.ordered_on, garment.status = silk, dt.date(2026, 10, 21), "ready"
        garment.save()
        log = AuditLog.objects.filter(event="update", model_label="dummyapp.Garment").latest("pk")
        self.assertEqual(str(log), f"Update Garment {garment.pk}")
        self.assertEqual(verbose_model_name("core.User"), "User")
        new, old = change_rows(log)
        self.assertIn(("Fabric", "Silk"), new)                  # related object by name, not id
        self.assertIn(("Fabric", "Cotton"), old)
        self.assertIn(("Status", "Ready"), new)                 # choice label
        self.assertIn(("Ordered on", "21-Oct-2026"), new)       # site date format

    def test_audit_page_shows_every_field_with_old_and_new(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        garment = Garment.objects.create(company=self.company_a, name="Polo", style_no="P", price=1)
        garment.price = 2
        garment.save()
        log = AuditLog.objects.filter(event="update").latest("pk")
        r = self.login(admin).get(reverse("core:resource_detail", args=["audit", log.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, "Update Garment")
        self.assertContains(r, '<span class="fw-medium">New:</span>', html=False)
        self.assertContains(r, '<span class="fw-medium">Old:</span>', html=False)
        self.assertContains(r, '<li class="audit-changed"><mark>Price: ', count=2)       # highlighted in New and in Old
        self.assertNotContains(r, "dummyapp.Garment")


class DateFormatTests(CoreTestCase):
    def test_default_site_format(self):
        self.assertEqual(format_value(dt.date(2026, 10, 21)), "21-Oct-2026")
        out = Template("{{ d|date }}|{{ d|date:'DATE_FORMAT' }}").render(Context({"d": dt.date(2026, 10, 21)}))
        self.assertEqual(out, "21-Oct-2026|21-Oct-2026")

    @override_settings(CORE_DATE_FORMAT="d/m/Y", CORE_DATETIME_FORMAT="d/m/Y H:i")
    def test_format_comes_from_settings_and_can_be_overridden(self):
        self.assertEqual(Template("{{ d|date }}").render(Context({"d": dt.date(2026, 10, 21)})), "21/10/2026")
        self.assertEqual(format_value(dt.date(2026, 10, 21), "Y.m.d"), "2026.10.21")         # one place overrides it

    def test_list_pages_use_the_resource_or_site_format(self):
        user = make_user("u@example.com", self.company_a, perms=PERMS)
        Garment.objects.create(company=self.company_a, name="Polo", style_no="P", ordered_on=dt.date(2026, 10, 21))
        from core.crud.registry import get_resource

        cfg = get_resource("garments")
        r = self.login(user).get(reverse("core:resource_list", args=["garments"]))
        self.assertEqual(r.status_code, 200)
        cfg.date_formats = {"date": "Y/m/d"}
        try:
            from core.crud.service import display_value

            self.assertEqual(display_value(Garment.objects.get(name="Polo"), "ordered_on", cfg.date_formats), "2026/10/21")
        finally:
            cfg.date_formats = {}


class ListPageTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("u@example.com", self.company_a, perms=PERMS)
        for i in range(65):
            Garment.objects.create(company=self.company_a, name=f"G{i:02d}", style_no=str(i))

    def test_pager_and_plain_first_column(self):
        r = self.login(self.user).get(reverse("core:resource_list", args=["garments"]))
        self.assertContains(r, "Showing 1 to 25 of 65 entries")
        for text in (">First<", ">Last<", ">3<"):
            self.assertContains(r, text)
        from django.core.paginator import Paginator

        from core.templatetags.core_tags import page_window

        pages = Paginator(range(200), 20)
        self.assertEqual(page_window(pages.page(1)), [1, 2, 3, None])
        self.assertEqual(page_window(pages.page(5)), [None, 4, 5, 6, None])
        self.assertEqual(page_window(pages.page(10)), [None, 8, 9, 10])
        self.assertNotContains(r, "btn btn-link p-0 text-start")          # the first cell is not a link

    def test_company_switcher_only_for_several_companies(self):
        r = self.login(self.user).get(reverse("core:dashboard"))
        self.assertNotContains(r, "core-company-switch")
        self.assertContains(r, "core-company-name")
        profile = self.user.profile
        profile.companies.add(make_company("C"))
        from core.permissions import cache

        cache.bump()
        r = self.login(self.user).get(reverse("core:dashboard"))
        self.assertContains(r, "core-company-switch")
        self.assertContains(r, "Change company")

    def test_switching_company_returns_to_the_current_page(self):
        other = make_company("C")
        self.user.profile.companies.add(other)
        from core.permissions import cache

        cache.bump()
        client = self.login(self.user)
        r = client.get(reverse("core:profile"))
        self.assertContains(r, f'name="next" value="{reverse("core:profile")}"')
        r = client.post(reverse("core:switch_company"), {"company": other.pk, "next": reverse("core:profile")})
        self.assertRedirects(r, reverse("core:profile"), fetch_redirect_response=False)
        r = client.post(reverse("core:switch_company"), {"company": other.pk, "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("core:dashboard"), fetch_redirect_response=False)

    def test_switching_company_goes_home_when_the_page_is_not_allowed_there(self):
        user = make_user("two@example.com", self.company_a, perms=("dummyapp.view_garment",))
        user.profile.companies.add(self.company_b)
        from core.permissions import cache

        cache.bump()
        garment = Garment.objects.create(company=self.company_a, name="Tee", style_no="S1", price=10)
        client = self.login(user)
        switch = lambda nxt: client.post(reverse("core:switch_company"), {"company": self.company_b.pk, "next": nxt})
        listing = reverse("core:resource_list", args=["garments"])
        detail = reverse("core:resource_detail", args=["garments", garment.pk])
        self.assertRedirects(switch(listing + "?q=x"), listing + "?q=x", fetch_redirect_response=False)
        self.assertRedirects(switch(detail), reverse("core:dashboard"), fetch_redirect_response=False)   # record of company A
        self.assertRedirects(switch(reverse("core:menu_list")), reverse("core:dashboard"), fetch_redirect_response=False)  # no permission
        self.assertRedirects(switch("/no/such/page/"), reverse("core:dashboard"), fetch_redirect_response=False)


class AuditSnapshotTests(CoreTestCase):
    def test_update_lists_every_field_and_highlights_the_changed_ones(self):
        from core.audit.display import render_changes, snapshot_rows

        fabric = Fabric.objects.create(company=self.company_a, name="Cotton")
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="S1", price=10, fabric=fabric)
        g.price = 12
        g.save()
        log = AuditLog.objects.filter(event="update", model_label="dummyapp.Garment", object_pk=str(g.pk)).first()
        rows = {label: (old, new, changed) for label, old, new, changed in snapshot_rows(log)}
        self.assertEqual(rows["Price"][2], True)
        self.assertEqual(rows["Name"], ("Tee", "Tee", False))                  # unchanged values are listed too
        self.assertEqual(rows["Fabric"][1], "Cotton")                          # related objects by name
        html = str(render_changes(log))
        self.assertIn('<li class="audit-changed"><mark>Price: 12', html)
        self.assertIn('<li class="audit-changed"><mark>Price: 10', html)
        self.assertIn("<li>Name: Tee</li>", html)                              # unchanged fields listed, not highlighted
        self.assertIn("1 of", html)

    def test_rows_without_a_snapshot_keep_the_new_old_list(self):
        from core.audit.display import render_changes

        log = AuditLog.objects.create(event="update", model_label="dummyapp.Garment", object_pk="1", changes={"name": ["A", "B"]})
        self.assertIn("Old:", str(render_changes(log)))
