"""Contract: ``core`` is a self-contained, droppable app. It never imports a business app, never needs django.contrib.admin, and a
business app plugs in only through registries and abstract base models."""
import ast
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

import core
from core.approvals.registry import get_target
from core.crud.registry import get_resource
from core.reports.registry import get_report_source
from core.search.registry import get_search

CORE_DIR = Path(core.__file__).parent
ALLOWED_TOP = {"core", "django", "django_filters", "import_export", "guardian", "django_q", "reportlab", "openpyxl", "yaml", "PIL"}


class ImportBoundaryTests(SimpleTestCase):
    def test_core_never_imports_business_apps_or_the_project(self):
        offenders = []
        for path in CORE_DIR.rglob("*.py"):
            if "migrations" in path.parts or "static" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
                for n in names:
                    if n.split(".")[0] in ("tests", "dummyapp", "config", "garments"):
                        offenders.append(f"{path.relative_to(CORE_DIR)}: {n}")
        self.assertEqual(offenders, [])

    def test_core_does_not_use_the_admin(self):
        hits = [str(p.relative_to(CORE_DIR)) for p in CORE_DIR.rglob("*.py") if "migrations" not in p.parts
                and p.name != "checks.py"                       # the system check that forbids the admin names it
                and re.search(r"django\.contrib\.admin|admin\.site|ModelAdmin", p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])
        self.assertFalse(apps.is_installed("django.contrib.admin"))

    def test_only_core_and_config_are_project_packages(self):
        top = {p.name for p in CORE_DIR.parent.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
        self.assertTrue({"core", "config"} <= top)
        self.assertLessEqual(top - {"core", "config", "tests"}, set())

    def test_user_model_is_swappable_not_hardcoded(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in CORE_DIR.rglob("*.py") if "migrations" not in p.parts and p.name != "identity.py")
        self.assertNotRegex(text, r"ForeignKey\(\s*[\"']core\.User[\"']")


class DummyBusinessAppTests(TestCase):
    def test_business_app_plugs_in_through_registries_only(self):
        self.assertEqual(get_resource("garments").model._meta.label, "dummyapp.Garment")
        self.assertIsNotNone(get_search("resource", "garments"))
        self.assertEqual(get_report_source("garments").key, "garments")
        self.assertEqual(get_target("garment").model._meta.label, "dummyapp.Garment")

    def test_duplicate_registration_is_an_error(self):
        from django.core.exceptions import ImproperlyConfigured

        from tests.dummyapp.models import Garment
        from core.crud.registry import register_resource

        with self.assertRaises(ImproperlyConfigured):
            register_resource(Garment, slug="garments")

    def test_settings_have_safe_defaults(self):
        from core import conf

        self.assertGreater(conf.get("CORE_MAX_PAGE_SIZE"), 0)
        self.assertFalse(conf.get("CORE_BACKUP_ALLOW_RESTORE"))

    def test_auth_user_model_resolves_to_core_user(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "core.User")
        self.assertEqual(get_user_model()._meta.label, "core.User")
