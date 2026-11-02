"""Pure-Python (no Django): log scrubbing and audit-snapshot redaction never keep secrets.
Run with:  python -m unittest tests.test_redaction_pure"""
import json
import logging
from unittest import TestCase

from core.audit.redaction import MASK, clean_value, diff_snapshots, is_sensitive_name, redact_mapping
from core.logging import JsonFormatter, RedactFilter, scrub


class AuditRedactionTests(TestCase):
    def test_sensitive_names_are_masked_at_any_depth(self):
        out = redact_mapping({"password": "x", "profile": {"api_key": "k", "name": "ok"}, "items": [{"token": "t", "n": 1}], "email": "a@b.c"})
        self.assertEqual(out["password"], MASK)
        self.assertEqual(out["profile"], {"api_key": MASK, "name": "ok"})
        self.assertEqual(out["items"], [{"token": MASK, "n": 1}])
        self.assertEqual(out["email"], "a@b.c")

    def test_names_that_look_like_secrets(self):
        for name in ("password", "new_password1", "SECRET_KEY", "auth_token", "password_hash", "Authorization", "csrfmiddlewaretoken", "sessionid", "private_key"):
            self.assertTrue(is_sensitive_name(name), name)
        for name in ("name", "price", "style_no", "company"):
            self.assertFalse(is_sensitive_name(name), name)

    def test_bytes_and_long_values_are_never_stored_raw(self):
        self.assertEqual(clean_value(b"secret-file-contents"), "<20 bytes>")
        self.assertTrue(len(clean_value("x" * 5000)) < 600)

    def test_diff_only_lists_changed_fields(self):
        self.assertEqual(diff_snapshots({"a": 1, "b": 2}, {"a": 1, "b": 3}), {"b": [2, 3]})
        self.assertEqual(set(diff_snapshots(None, {"a": 1, "b": 2})), {"a", "b"})


class LogScrubTests(TestCase):
    def test_scrub_masks_credentials_urls_and_hashes(self):
        text = scrub("login password=hunter2 token: abc123 postgres://u:pw0rd@h/db hash pbkdf2_sha256$600000$salt$digest argon2$argon2id$v=19$m=1")
        for secret in ("hunter2", "abc123", "pw0rd", "digest", "argon2id"):
            self.assertNotIn(secret, text)

    def test_filter_and_json_formatter(self):
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed password=%s", ("s3cret",), None)
        RedactFilter().filter(record)
        line = JsonFormatter().format(record)
        self.assertNotIn("s3cret", line)
        data = json.loads(line)
        self.assertEqual((data["level"], data["logger"]), ("ERROR", "t"))
