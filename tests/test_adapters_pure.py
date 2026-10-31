"""Pure-Python adapter tests (no Django, no database). Run with:  python -m unittest tests.test_adapters_pure"""
from unittest import TestCase

from core.backup.adapters.base import ConnectionSettings, UnsupportedEngineError
from core.backup.adapters.mysql import MySQLAdapter
from core.backup.adapters.identifiers import InvalidIdentifier, quote_double, validate_identifier
from core.backup.adapters.redact import redact
from core.backup.adapters.registry import classify_engine
from core.backup.adapters.timestamps import KIND_DATE, KIND_DATETIME, KIND_OTHER, choose_creation_column


class IdentifierTests(TestCase):
    def test_quoting(self):
        self.assertEqual(quote_double("orders"), '"orders"')
        self.assertEqual(MySQLAdapter().quote("orders"), "`orders`")
        with self.assertRaises(InvalidIdentifier):
            MySQLAdapter().quote("a`b")

    def test_rejects_unsafe_names(self):
        for bad in ('a"b', "a`b", "a b", "a;drop", "", "1abc", "x" * 65, "a-b"):
            with self.assertRaises(InvalidIdentifier):
                validate_identifier(bad)


class RedactTests(TestCase):
    def test_masks_known_and_pattern_secrets(self):
        text = redact("failed: postgres://bob:s3cret@db/x PGPASSWORD=s3cret --password=hunter2", ["s3cret"])
        self.assertNotIn("s3cret", text)
        self.assertNotIn("hunter2", text)

    def test_connection_settings_never_show_password(self):
        conn = ConnectionSettings("postgresql", "h", "5432", "u", "db", "topsecret")
        self.assertNotIn("topsecret", repr(conn))
        self.assertNotIn("topsecret", str(conn))
        self.assertNotIn("topsecret", conn.redact("error topsecret"))


class EngineTests(TestCase):
    def test_supported_and_unsupported(self):
        self.assertEqual(classify_engine("django.db.backends.postgresql"), "postgresql")
        self.assertEqual(classify_engine("django.db.backends.sqlite3"), "sqlite")
        self.assertEqual(classify_engine("django.db.backends.mysql"), "mysql")             # MariaDB uses the same Django backend
        for engine in ("django.db.backends.oracle", ""):
            with self.assertRaises(UnsupportedEngineError):
                classify_engine(engine)


class CreationColumnTests(TestCase):
    def test_prefers_created_at_datetime(self):
        choice = choose_creation_column([("id", KIND_OTHER), ("updated_at", KIND_DATETIME), ("created_at", KIND_DATETIME)])
        self.assertEqual(choice.column, "created_at")

    def test_never_guesses(self):
        self.assertIsNone(choose_creation_column([("id", KIND_OTHER), ("updated_at", KIND_DATETIME)]).column)
        self.assertIsNone(choose_creation_column([("id", KIND_OTHER), ("created_on", KIND_DATE)]).column)
        self.assertIsNone(choose_creation_column([("id", KIND_OTHER), ("name", KIND_OTHER)]).column)
        self.assertTrue(choose_creation_column([("id", KIND_OTHER)]).reason)
