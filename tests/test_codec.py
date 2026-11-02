"""Pure-Python archive format tests (need no database). The authoring session ran an equivalent round-trip script by hand."""
import datetime as dt
import decimal
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase

from core.archive import codec


class CodecTests(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "b.jsonl.gz"
        self.rows = [
            [1, "a", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), decimal.Decimal("1.50"), b"\x00\x01", None, uuid.uuid4()],
            [2, "b", dt.datetime(2026, 1, 2), decimal.Decimal("2"), b"", True, uuid.uuid4()],
        ]
        self.cols = ["id", "n", "c", "d", "b", "x", "u"]

    def test_round_trip_keeps_types(self):
        info = codec.write_batch(self.path, "t", self.cols, "id", self.rows)
        header, rows = codec.read_batch(self.path)
        self.assertEqual(list(rows), self.rows)
        self.assertEqual(header["pk"], "id")
        self.assertEqual((info["pk_min"], info["pk_max"]), ("1", "2"))
        self.assertIsNone(codec.verify_batch(self.path, 2, info["sha256"], info["pk_sha256"]))

    def test_verification_detects_tampering_and_wrong_counts(self):
        info = codec.write_batch(self.path, "t", self.cols, "id", self.rows)
        self.assertIn("row count", codec.verify_batch(self.path, 3, info["sha256"], info["pk_sha256"]))
        self.assertIn("key digest", codec.verify_batch(self.path, 2, info["sha256"], "0" * 64))
        data = bytearray(self.path.read_bytes())
        data[-12] ^= 0xFF
        self.path.write_bytes(bytes(data))
        self.assertIsNotNone(codec.verify_batch(self.path, 2, info["sha256"], info["pk_sha256"]))
        self.assertEqual(codec.verify_batch(self.path.with_name("none"), 1, "x", "y"), "archive file is missing")

    def test_unknown_type_fails_before_a_file_exists(self):
        with self.assertRaises(codec.ArchiveEncodeError):
            codec.write_batch(self.path, "t", ["id", "v"], "id", [[1, object()]])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_suffix(".gz.part").exists())

    def test_pk_digest_is_order_independent(self):
        self.assertEqual(codec.pk_digest([1, 2, 3]), codec.pk_digest([3, 1, 2]))
