"""One validation path for every upload (CRUD forms, imports, API): size, extension allowlist, no path tricks, image verification."""
from __future__ import annotations

import os

from django.core.exceptions import ValidationError

from core import conf

_SIGNATURES = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".gif": (b"GIF87a", b"GIF89a"),
               ".pdf": (b"%PDF-",), ".webp": (b"RIFF",), ".xlsx": (b"PK\x03\x04",), ".docx": (b"PK\x03\x04",), ".odt": (b"PK\x03\x04",),
               ".ods": (b"PK\x03\x04",)}


def validate_upload(file, *, images_only: bool = False, max_bytes: int | None = None, extensions=None) -> None:
    raw = str(getattr(file, "name", "") or "")
    name = os.path.basename(raw.replace("\\", "/"))
    if not name or "\x00" in raw or name in (".", ".."):
        raise ValidationError("Invalid file name.")
    ext = os.path.splitext(name)[1].lower()
    allowed = tuple(extensions or (conf.get("CORE_IMAGE_EXTENSIONS") if images_only else conf.get("CORE_UPLOAD_ALLOWED_EXTENSIONS")))
    if ext not in allowed:
        raise ValidationError("This file type is not allowed. Allowed: %(types)s.", params={"types": ", ".join(allowed)}, code="type")
    limit = max_bytes or conf.get("CORE_UPLOAD_MAX_BYTES")
    if file.size > limit:
        raise ValidationError("The file is too large (maximum %(mb).1f MB).", params={"mb": limit / 1048576}, code="size")
    signatures = _SIGNATURES.get(ext)
    if signatures:
        pos = file.tell() if hasattr(file, "tell") else 0
        head = file.read(16)
        file.seek(pos)
        if not any(head.startswith(sig) for sig in signatures):
            raise ValidationError("The file content does not match its extension.", code="content")
    if images_only:
        try:
            from PIL import Image

            pos = file.tell()
            Image.open(file).verify()
            file.seek(pos)
        except Exception:
            raise ValidationError("This is not a valid image.", code="image") from None
