from django.db import transaction
from django.urls import reverse

from core.menu.service import invalidate_menus, menu_for, register_menu_entry, validate_parent
from core.models import MenuItem, Notification
from core.notifications.service import mark_read, notify, unread_count

from .base import CoreTestCase, make_user


class NotificationTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("n@example.com", self.company_a)

    def test_notify_runs_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            notify([self.user], "Hello", company=self.company_a)
            self.assertEqual(Notification.objects.count(), 0)
        for cb in callbacks:
            cb()
        self.assertEqual(Notification.objects.filter(recipient=self.user).count(), 1)

    def test_rolled_back_transaction_sends_nothing(self):
        try:
            with transaction.atomic():
                notify([self.user], "Ghost")
                raise RuntimeError
        except RuntimeError:
            pass
        self.assertEqual(Notification.objects.count(), 0)

    def test_read_state_and_isolation(self):
        other = make_user("o@example.com", self.company_a)
        with self.captureOnCommitCallbacks(execute=True):
            notify([self.user, other], "x")
        self.assertEqual(unread_count(self.user), 1)
        n = Notification.objects.get(recipient=other)
        self.assertEqual(mark_read(self.user, [n.pk]), 0)             # cannot mark someone else's
        self.assertEqual(mark_read(self.user), 1)

    def test_open_redirects_only_to_internal_links(self):
        n = Notification.objects.create(recipient=self.user, title="t", url="https://evil.example/x")
        r = self.login(self.user).get(reverse("core:notification_open", args=[n.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(r["Location"].startswith("https://evil"))

    def test_email_is_opt_in(self):
        from django.core import mail

        with self.captureOnCommitCallbacks(execute=True):
            notify([self.user], "No email by default")
        self.assertEqual(len(mail.outbox), 0)


class MenuTests(CoreTestCase):
    def test_db_menu_is_permission_filtered_and_cache_invalidates(self):
        user = make_user("m@example.com", self.company_a)
        parent = MenuItem.objects.create(title="Ops", position=1)
        MenuItem.objects.create(title="Secret", url_name="core:backup_list", parent=parent, permission="core.view_backuprecord")
        MenuItem.objects.create(title="Open", url_name="core:dashboard", parent=parent)
        titles = lambda: [c.title for e in menu_for(user, self.company_a.pk) if e.title == "Ops" for c in e.children]
        self.assertEqual(titles(), ["Open"])
        MenuItem.objects.filter(title="Open").update(is_active=False)
        invalidate_menus()
        self.assertEqual(titles(), [])

    def test_four_level_menu_renders_in_sidebar(self):
        user = make_user("deep@example.com", self.company_a)
        parent = None
        for title in ("Lvl1", "Lvl2", "Lvl3"):
            parent = MenuItem.objects.create(title=title, parent=parent)
        MenuItem.objects.create(title="Lvl4Leaf", path="/reports/", parent=parent)
        invalidate_menus()
        html = self.login(user).get(reverse("core:dashboard")).content.decode()
        for title in ("Lvl1", "Lvl2", "Lvl3", "Lvl4Leaf"):
            self.assertIn(title, html)

    def test_menu_depth_is_limited(self):
        from django.core.exceptions import ValidationError

        parent = None
        for n in range(1, 7):
            parent = MenuItem.objects.create(title=f"D{n}", path="/", parent=parent)
        with self.assertRaises(ValidationError):
            MenuItem.objects.create(title="D7", path="/", parent=parent)
        branch = MenuItem.objects.create(title="B1", path="/")
        MenuItem.objects.create(title="B2", path="/", parent=branch)
        branch.parent = MenuItem.objects.get(title="D5")       # B1 -> level 6, its child would be level 7
        with self.assertRaises(ValidationError):
            branch.save()

    def test_cycles_are_refused(self):
        a = MenuItem.objects.create(title="A")
        b = MenuItem.objects.create(title="B", parent=a)
        self.assertFalse(validate_parent(a.pk, b.pk))
        self.assertTrue(validate_parent(b.pk, None))

    def test_menu_visibility_is_not_authorisation(self):
        user = make_user("v@example.com", self.company_a)
        MenuItem.objects.create(title="Backups", url_name="core:backup_list")
        self.assertEqual(self.login(user).get(reverse("core:backup_list")).status_code, 403)

    def test_registered_entries_appear(self):
        user = make_user("r@example.com", self.company_a)
        register_menu_entry("Dash2", "core:dashboard")
        invalidate_menus()
        self.assertIn("Dash2", [e.title for e in menu_for(user, self.company_a.pk)])


def _descendants(entry):
    for child in entry.children:
        yield child
        yield from _descendants(child)


class SeededMenuTests(CoreTestCase):
    def test_defaults_are_database_rows_created_by_migrate(self):
        titles = set(MenuItem.objects.values_list("title", flat=True))
        for wanted in ("Dashboard", "Approvals", "Reports", "Notifications", "Administration", "Companies", "Users", "Audit log", "Menu Builder", "Common", "Auth", "Approval Builder"):
            self.assertIn(wanted, titles)
        admin = MenuItem.objects.get(title="Administration")
        self.assertEqual((admin.superuser_only, admin.permission), (False, "core.is_staff"))      # staff see Roles / Permissions
        self.assertTrue(MenuItem.objects.get(title="Companies").superuser_only)
        self.assertEqual(MenuItem.objects.get(title="Roles").permission, "core.is_staff")

    def test_seeding_is_idempotent_and_never_overwrites_edits(self):
        from core.menu.defaults import seed

        MenuItem.objects.filter(title="Dashboard").update(title="Home", position=5)
        before = MenuItem.objects.count()
        self.assertEqual(seed(MenuItem), 1)                    # only the renamed default is re-added
        self.assertEqual(MenuItem.objects.count(), before + 1)
        self.assertEqual(seed(MenuItem), 0)
        self.assertTrue(MenuItem.objects.filter(title="Home", position=5).exists())

    def test_every_default_row_uses_a_url_name_that_resolves(self):
        from django.urls import reverse as rev

        self.assertFalse(MenuItem.objects.filter(parent__title="Administration").exclude(path="").exists())      # no literal paths
        for row in MenuItem.objects.exclude(url_name=""):
            self.assertTrue(rev(row.url_name), row.url_name)
        self.assertTrue(rev("core:companies").endswith("/r/companies/"))

    def test_menu_page_lists_seeded_rows(self):
        admin = make_user("seedroot@example.com", self.company_a, superuser=True)
        self.assertContains(self.login(admin).get(reverse("core:menu_list")), "Administration")

    def test_plain_users_see_only_non_superuser_rows(self):
        plain = make_user("seedplain@example.com", self.company_a)
        top = [e.title for e in menu_for(plain, self.company_a.pk)]
        self.assertIn("Dashboard", top)
        self.assertNotIn("Administration", top)


class CoreAdminMenuTests(CoreTestCase):
    def test_default_core_menu_is_superuser_only_and_complete(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        plain = make_user("plain@example.com", self.company_a, perms=["core.view_auditlog", "core.change_menuitem"])
        titles = lambda u: [c.title for e in menu_for(u, self.company_a.pk) if e.title == "Administration" for c in _descendants(e)]
        for wanted in ("Companies", "Departments", "Designations", "Users", "Approval Types", "Approval Steps", "Menu Builder", "Saved Reports"):
            self.assertIn(wanted, titles(admin))
        self.assertEqual(titles(plain), [])                  # permissions alone never reveal the core admin menu

    def test_default_layout_order(self):
        admin = make_user("root8@example.com", self.company_a, superuser=True)
        tree = {e.title: e for e in menu_for(admin, self.company_a.pk)}
        self.assertEqual([t for t in tree if t in ("Dashboard", "Administration", "Approvals", "Reports", "Mail", "Notifications")],
                         ["Dashboard", "Administration", "Approvals", "Reports", "Mail", "Notifications"])
        children = {c.title: [g.title for g in c.children] for c in tree["Administration"].children}
        self.assertEqual(list(children), ["Common", "Users", "Menu Builder", "Approval Builder", "Auth", "Notification Rules",
                                          "Saved Reports", "Search builder", "Backups", "Archive", "Export jobs", "Background jobs",
                                          "Audit log"])
        self.assertEqual(children["Common"], ["Companies", "Departments", "Designations"])
        self.assertEqual(children["Approval Builder"], ["Approval Types", "Approval Steps", "Approval Requests"])
        self.assertEqual(children["Auth"], ["Permissions", "Roles"])

    def test_audit_log_is_under_administration_for_superusers_and_staff(self):
        admin = make_user("root9@example.com", self.company_a, superuser=True)
        staff = make_user("staff9@example.com", self.company_a)
        staff.is_staff = True
        staff.save()
        plain = make_user("plain9@example.com", self.company_a, perms=["core.view_auditlog"])
        top = lambda u: [c.title for e in menu_for(u, self.company_a.pk) if e.title == "Administration" for c in e.children]
        self.assertIn("Audit log", top(admin))
        self.assertIn("Audit log", top(staff))
        self.assertNotIn("Audit log", top(plain))
        self.assertEqual(self.login(staff).get(reverse("core:audit_log")).status_code, 200)
        self.assertEqual(self.login(plain).get(reverse("core:audit_log")).status_code, 403)

    def test_core_admin_links_open_for_superuser(self):
        admin = make_user("root2@example.com", self.company_a, superuser=True)
        client = self.login(admin)
        for entry in [c for e in menu_for(admin, self.company_a.pk) if e.title == "Administration" for c in _descendants(e) if c.url]:
            self.assertEqual(client.get(entry.url).status_code, 200, entry.title)

    def test_approval_type_with_steps_can_be_configured_in_the_ui(self):
        from core.models import ApprovalStep, ApprovalType

        admin = make_user("root3@example.com", self.company_a, superuser=True)
        step = ApprovalStep.objects.create(code="s1", name="S1")
        r = self.login(admin).post(reverse("core:resource_new", args=["approval-types"]), {
            "name": "T", "code": "t", "reject_mode": "return_to_submitter", "is_active": "on",
            "steps-TOTAL_FORMS": "1", "steps-INITIAL_FORMS": "0", "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000",
            "steps-0-step": str(step.pk), "steps-0-position": "0", "steps-0-is_final": "on", "steps-0-is_enabled": "on"})
        self.assertEqual(r.status_code, 302, r.content[:300])
        self.assertTrue(ApprovalType.objects.get(code="t").type_steps.filter(is_final=True).exists())


class ThemeTests(CoreTestCase):
    def test_default_is_system_and_it_can_be_saved(self):
        user = make_user("th@example.com", self.company_a)
        self.assertEqual(user.profile.theme, "system")
        client = self.login(user)
        page = client.get(reverse("core:dashboard"))
        self.assertContains(page, 'data-theme-pref="system"')
        self.assertContains(page, "data-theme-toggle")
        self.assertNotContains(page, 'data-bs-theme="dark"')        # the sidebar follows the theme
        self.assertNotContains(page, "data-theme-set")             # a single toggle, no dropdown
        self.assertEqual(client.post(reverse("core:theme"), {"theme": "dark"}).status_code, 204)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(client.post(reverse("core:theme"), {"theme": "neon"}).status_code, 400)


class PagePermissionTests(CoreTestCase):
    """Approvals, Reports, Mail and Notifications need their permission (menu entry and page); Dashboard is open to everyone."""

    def test_pages_and_top_bar_follow_the_page_permissions(self):
        from django.urls import reverse

        from core.menu.service import menu_for
        from core.permissions import PAGE_PERMS

        bare = make_user("bare@example.com", self.company_a, page_perms=False)
        client = self.login(bare)
        pages = ("core:approval_queue", "core:approval_mine", "core:report_list", "core:mail_inbox", "core:mail_badge",
                 "core:notification_list", "core:notification_badge", "core:notification_dropdown")
        for name in pages:
            self.assertEqual(client.get(reverse(name)).status_code, 403, name)
        home = client.get(reverse("core:dashboard"))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, 'id="core-mail-badge"')
        self.assertNotContains(home, 'aria-label="Notifications"')
        titles = {e.title for e in menu_for(bare, self.company_a.pk)}
        self.assertIn("Dashboard", titles)
        self.assertFalse({"Approvals", "Reports", "Mail", "Notifications"} & titles)

        full = make_user("full@example.com", self.company_a)
        client = self.login(full)
        for name in pages:
            self.assertEqual(client.get(reverse(name)).status_code, 200, name)
        self.assertContains(client.get(reverse("core:dashboard")), 'id="core-mail-badge"')
        titles = {e.title for e in menu_for(full, self.company_a.pk)}
        self.assertTrue({"Dashboard", "Approvals", "Reports", "Mail", "Notifications"} <= titles)
        self.assertEqual(len(PAGE_PERMS), 4)

    def test_every_default_link_except_dashboard_has_a_permission(self):
        from core.menu.defaults import DEFAULT_MENU

        def walk(items):
            for title, _icon, url_name, path, permission, _su, children in items:
                if url_name or path:
                    self.assertTrue(permission or url_name == "core:dashboard", title)
                walk(children)

        walk(DEFAULT_MENU)


class MenuTreePageTests(CoreTestCase):
    """Menu builder list: a tree table with groups, children counts, who-sees-it badges and hidden entries."""

    def test_tree_rows_and_page(self):
        from core.views.menu import tree_rows

        group = MenuItem.objects.create(title="Shop", icon="building", permission="core.is_staff", position=0, is_active=False)
        MenuItem.objects.create(title="Orders", url_name="core:dashboard", parent=group, position=0)
        MenuItem.objects.create(title="Stock", url_name="core:dashboard", parent=group, position=1, superuser_only=True)
        rows = {r["item"].title: r for r in tree_rows()}
        self.assertEqual((rows["Shop"]["children"], rows["Orders"]["depth"]), (2, 1))
        self.assertEqual((rows["Orders"]["first"], rows["Orders"]["last"], rows["Stock"]["last"]), (True, False, True))
        self.assertEqual((rows["Shop"]["off"], rows["Orders"]["off_by_parent"]), (True, True))     # under a hidden group
        root = make_user("treeroot@example.com", self.company_a, superuser=True)
        html = self.login(root).get(reverse("core:menu_list")).content.decode()
        head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
        for label in ("Title", "Target", "Permission", "Superuser only", "Active"):
            self.assertIn(f"<th>{label}</th>", head)
        for text in ("2 items", ">Staff<", "group hidden", "ti-building"):
            self.assertIn(text, html)
