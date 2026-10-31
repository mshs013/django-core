"""Pure file-backup tests (no Django): selection, exclusions, symlink safety, validation, safe extraction.
Run with:  python -m unittest tests.test_files_files"""
import os
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase

from core.backup import fileselect


class FileBackupTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / "data"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "keep.txt").write_text("keep")
        (self.root / "sub" / "a.txt").write_text("a")
        (self.root / "debug.log").write_text("log")
        (self.root / ".hidden").write_text("h")
        self.outside = base / "secret.txt"
        self.outside.write_text("TOP SECRET")
        os.symlink(self.outside, self.root / "evil-link")
        self.dest = base / "out.tar.gz"

    def _rules(self, **kw):
        return fileselect.ExclusionRules.build(self.root, config_patterns=["*.log"], **kw)

    def test_only_configured_sources_can_be_picked(self):
        sources = fileselect.parse_sources([f"Data={self.root}"])
        self.assertEqual(fileselect.pick_source(sources, "0").path, self.root)
        for bad in ("5", "../../etc", None):
            with self.assertRaises(fileselect.FileBackupError):
                fileselect.pick_source(sources, bad)

    def test_exclusions_and_symlink_to_outside_are_not_followed(self):
        stats = fileselect.build_archive(self.dest, self.root, self._rules())
        with tarfile.open(self.dest) as tar:
            names = tar.getnames()
            contents = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isreg())
        self.assertIn("data/keep.txt", names)
        self.assertIn("data/sub/a.txt", names)
        self.assertNotIn("data/debug.log", names)
        self.assertNotIn(b"TOP SECRET", contents)
        self.assertGreaterEqual(stats["files"], 2)

    def test_hidden_files_can_be_excluded(self):
        fileselect.build_archive(self.dest, self.root, self._rules(include_hidden=False))
        with tarfile.open(self.dest) as tar:
            self.assertNotIn("data/.hidden", tar.getnames())

    def test_extra_exclusion_outside_root_is_refused(self):
        with self.assertRaises(fileselect.FileBackupError):
            fileselect.ExclusionRules.build(self.root, extra=["../secret.txt"])

    def test_validate_and_safe_extract(self):
        stats = fileselect.build_archive(self.dest, self.root, self._rules())
        self.assertTrue(fileselect.validate_archive(self.dest, stats["files"]).startswith("ok"))
        target = Path(self.tmp.name) / "restore"
        target.mkdir()
        self.assertEqual(fileselect.safe_extract(self.dest, target), stats["files"])
        self.assertEqual((target / "data" / "keep.txt").read_text(), "keep")

    def test_traversal_member_is_rejected(self):
        evil = Path(self.tmp.name) / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            import io
            tar.addfile(info, io.BytesIO(b"x"))
        target = Path(self.tmp.name) / "r2"
        target.mkdir()
        with self.assertRaises(fileselect.FileBackupError):
            fileselect.safe_extract(evil, target)
        self.assertFalse((Path(self.tmp.name) / "escape.txt").exists())
