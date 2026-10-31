"""Supported database engines: the ``core.E030`` system check and ``manage.py dbinfo``."""
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from core.checks import database_engines


class EngineCheckTests(SimpleTestCase):
    def test_supported_engines_pass(self):
        for engine in ("django.db.backends.postgresql", "django.db.backends.mysql", "django.db.backends.sqlite3"):
            with override_settings(DATABASES={"default": {"ENGINE": engine, "NAME": "x"}}):
                self.assertEqual(database_engines(None), [], engine)

    def test_unsupported_engine_is_an_error(self):
        with override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.oracle", "NAME": "x"}}):
            errors = database_engines(None)
        self.assertEqual([e.id for e in errors], ["core.E030"])
        self.assertIn("default", errors[0].msg)


class DbInfoTests(TestCase):
    def test_reports_engine_without_password(self):
        out = StringIO()
        call_command("dbinfo", "--live", stdout=out)
        text = out.getvalue()
        engine = {"sqlite3": "sqlite", "postgresql": "postgresql", "mysql": "mysql"}[settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]]
        self.assertIn(f"engine   {engine}", text)
        self.assertIn("server   ", text)
        password = settings.DATABASES["default"].get("PASSWORD")
        if password:
            self.assertNotIn(password, text)
