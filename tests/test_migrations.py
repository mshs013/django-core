"""One clean migration: no missing migrations, default data after a fresh ``migrate``, seeding is idempotent and never undoes an
administrator's menu changes."""
import io
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db.migrations.loader import MigrationLoader
from django.db.models.signals import post_migrate
from django.test import TestCase

from core.menu.defaults import DEFAULT_MENU
from core.models import MenuItem

MIGRATIONS = Path(__file__).resolve().parent.parent / "core" / "migrations"


def menu_rows():
    return sorted(MenuItem.objects.values_list("title", "parent__title", "icon", "url_name", "path", "permission", "superuser_only",
                                               "position"), key=lambda r: (r[1] or "", r[7], r[0]))


def default_rows(tree=DEFAULT_MENU, parent=None):
    out = []
    for position, (title, icon, url_name, path, permission, superuser_only, children) in enumerate(tree):
        out.append((title, parent, icon, url_name, path, permission, superuser_only, (position + 1) * 10))
        out += default_rows(children, title)
    return sorted(out, key=lambda r: (r[1] or "", r[7], r[0]))


def emit_post_migrate():
    post_migrate.send(sender=apps.get_app_config("core"), app_config=apps.get_app_config("core"), verbosity=0, interactive=False,
                      using="default", apps=apps, plan=[])


class MigrationTests(TestCase):
    def test_model_changes_have_a_migration(self):
        out = io.StringIO()
        try:
            call_command("makemigrations", "core", check=True, dry_run=True, stdout=out, stderr=out)
        except SystemExit:
            self.fail("core models changed without a migration:\n" + out.getvalue())

    def test_core_has_one_initial_migration_and_a_linear_chain(self):
        names = sorted(p.stem for p in MIGRATIONS.glob("0*.py"))
        self.assertEqual(names[0], "0001_initial")
        loader = MigrationLoader(None, ignore_no_migrations=True)
        self.assertEqual([n for n in names if loader.get_migration("core", n).initial], ["0001_initial"])
        self.assertEqual(loader.graph.leaf_nodes("core"), [("core", names[-1])])           # one chain: 0001 -> 0002 -> ...
        self.assertEqual([name for _app, name in loader.graph.forwards_plan(("core", names[-1])) if _app == "core"], names)

    def test_initial_migration_holds_no_data_operations(self):      # fresh data: post_migrate / bootstrap; later ones may (UPGRADING §3)
        from django.db.migrations import RunPython, RunSQL

        migration = MigrationLoader(None, ignore_no_migrations=True).get_migration("core", "0001_initial")
        self.assertFalse([op for op in migration.operations if isinstance(op, (RunPython, RunSQL))])

    def test_fresh_database_has_the_default_menu(self):              # the test database was built by ``migrate``
        self.assertEqual(menu_rows(), default_rows())

    def test_fresh_database_has_the_permissions(self):
        perms = set(Permission.objects.filter(content_type__app_label="core").values_list("codename", flat=True))
        for codename in ("add_step_to_request", "build_search", "run_archive", "restore_archive", "view_user", "view_menuitem",
                         "view_approvalrequest", "view_reportdefinition", "view_mailmessage", "view_notification"):
            self.assertIn(codename, perms)

    def test_seeding_twice_changes_nothing(self):
        before = menu_rows()
        emit_post_migrate()
        call_command("seed_default_menu", stdout=io.StringIO())
        call_command("seed_default_menu", stdout=io.StringIO())
        self.assertEqual(menu_rows(), before)

    def test_post_migrate_never_undoes_administrator_changes(self):
        MenuItem.objects.filter(title="Mail").delete()
        MenuItem.objects.filter(title="Reports", parent=None).update(title="My reports", superuser_only=True)
        emit_post_migrate()
        self.assertFalse(MenuItem.objects.filter(title="Mail").exists())      # a deleted row stays deleted
        self.assertTrue(MenuItem.objects.get(title="My reports").superuser_only)
        self.assertFalse(MenuItem.objects.filter(title="Reports", parent=None).exists())

    def test_post_migrate_seeds_an_empty_menu_and_the_command_adds_missing_rows(self):
        MenuItem.objects.all().delete()
        emit_post_migrate()
        self.assertEqual(menu_rows(), default_rows())
        MenuItem.objects.filter(title="Mail").delete()
        call_command("seed_default_menu", stdout=io.StringIO())             # explicit: adds what is missing
        self.assertTrue(MenuItem.objects.filter(title="Mail", parent=None).exists())
