import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from core.files import service, validators
from core.models import StoredFile

from .base import CoreTestCase, make_user


class UploadTests(CoreTestCase):
    def test_extension_allowlist_and_double_extension(self):
        for bad in ("a.exe", "a.php", "a.pdf.exe", "../../etc/passwd", "a.html", ""):
            with self.assertRaises(ValidationError, msg=bad):
                validators.validate_upload(SimpleUploadedFile(bad or " ", b"x"))
        validators.validate_upload(SimpleUploadedFile("ok.pdf", b"%PDF-1.4"))

    def test_size_limit(self):
        with self.assertRaises(ValidationError):
            validators.validate_upload(SimpleUploadedFile("a.pdf", b"x" * 2048), max_bytes=1024)

    def test_images_are_verified_by_content_not_name(self):
        with self.assertRaises(ValidationError):
            validators.validate_upload(SimpleUploadedFile("fake.png", b"not an image"), images_only=True)

    def test_storage_name_is_random_and_original_name_never_a_path(self):
        user = make_user("u@example.com", self.company_a)
        stored = service.save_upload(SimpleUploadedFile("my report.pdf", b"%PDF-1.4 x"), user=user, company=self.company_a)
        self.assertNotIn("report", stored.storage_name)
        self.assertTrue(service.safe_path(stored.storage_name).exists())

    def test_path_escape_is_refused(self):
        for bad in ("../x", "/etc/passwd", "a/../../b", "a\x00b"):
            with self.assertRaises(service.PathEscape):
                service.safe_path(bad)

    def test_private_download_requires_company_and_returns_nosniff(self):
        owner = make_user("o@example.com", self.company_a)
        stranger = make_user("s@example.com", self.company_b)
        stored = service.save_upload(SimpleUploadedFile("a.pdf", b"%PDF-1.4"), user=owner, company=self.company_a)
        self.assertTrue(service.can_download(owner, stored))
        self.assertFalse(service.can_download(stranger, stored))
        r = service.response_for(owner, stored)
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", r["Content-Disposition"])

    def test_orphan_cleanup_respects_grace_and_dry_run(self):
        user = make_user("c@example.com", self.company_a)
        stored = service.save_upload(SimpleUploadedFile("a.pdf", b"%PDF-1.4"), user=user, company=self.company_a)
        self.assertEqual(service.cleanup_orphans(dry_run=True)["rows"], 0)          # inside the grace window
        with override_settings(CORE_ORPHAN_GRACE_HOURS=-1):
            self.assertEqual(service.cleanup_orphans(dry_run=True)["rows"], 1)
            self.assertTrue(StoredFile.objects.filter(pk=stored.pk).exists())
            self.assertEqual(service.cleanup_orphans()["rows"], 1)
        self.assertFalse(StoredFile.objects.filter(pk=stored.pk).exists())
