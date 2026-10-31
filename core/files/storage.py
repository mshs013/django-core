"""Private file storage: lives OUTSIDE static/media roots, has no URL, and is only served by authorised views."""
from __future__ import annotations

import os
import uuid

from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

from core import conf


@deconstructible
class PrivateStorage(FileSystemStorage):
    def __init__(self, **kwargs):
        super().__init__(location=str(conf.private_media_root()), base_url=None, file_permissions_mode=0o600,
                         directory_permissions_mode=0o700)

    def url(self, name):  # never public
        raise ValueError("Private files have no public URL; use the authorised download view.")

    def deconstruct(self):  # keep migrations independent of the machine-specific path
        return ("core.files.storage.PrivateStorage", [], {})


private_storage = PrivateStorage()


@deconstructible
class RandomUploadTo:
    """``upload_to`` that discards the client's file name: ``<prefix>/<yy>/<mm>/<uuid4 hex><ext>`` (extension lower-cased, allowlisted)."""

    def __init__(self, prefix: str = "uploads"):
        self.prefix = prefix.strip("/").replace("..", "")

    def __call__(self, instance, filename: str) -> str:
        import datetime as dt

        ext = os.path.splitext(filename)[1].lower()
        if not ext or len(ext) > 10 or not ext[1:].isalnum():
            ext = ""
        now = dt.date.today()
        return f"{self.prefix}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{ext}"

    def __eq__(self, other):
        return isinstance(other, RandomUploadTo) and other.prefix == self.prefix


def random_upload_to(prefix: str = "uploads") -> RandomUploadTo:
    return RandomUploadTo(prefix)
