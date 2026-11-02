"""Upload validation shared by CRUD forms, API-style endpoints and imports (one code path)."""
from __future__ import annotations

import os
import re
import secrets

from django.core.exceptions import ValidationError

from core import conf

_BAD_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)
MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",), ".pdf": (b"%PDF-",), ".xlsx": (b"PK\x03\x04",), ".docx": (b"PK\x03\x04",), ".ods": (b"PK\x03\x04",),
    ".odt": (b"PK\x03\x04",),
}


def sanitize_name(name: str) -> str:
    """Display-only name: basename, no separators/control chars, bounded length. NEVER used as a filesystem path."""
    base = os.path.basename((name or "").replace("\\", "/")).strip().strip(".")
    base = _BAD_NAME.sub("_", base)[:120]
    return base or "file"


def random_storage_name(extension: str = "") -> str:
    ext = extension.lower() if re.fullmatch(r"\.[a-z0-9]{1,8}", extension.lower()) else ""
    return secrets.token_hex(16) + ext


def validate_upload(uploaded, *, allowed_extensions=None, max_bytes: int | None = None) -> str:
    """Raise ValidationError, or return the lower-case extension. Checks size, extension allowlist, magic bytes, and decodes images."""
    allowed = tuple(allowed_extensions or conf.get("CORE_UPLOAD_ALLOWED_EXTENSIONS"))
    limit = max_bytes or conf.get("CORE_UPLOAD_MAX_BYTES")
    name = sanitize_name(getattr(uploaded, "name", ""))
    ext = os.path.splitext(name)[1].lower()
    if ext not in allowed:
        raise ValidationError(f"File type '{ext or 'none'}' is not allowed.", code="file_type")
    size = getattr(uploaded, "size", None)
    if size is None or size > limit:
        raise ValidationError(f"File is larger than the {limit // (1024 * 1024)} MB limit.", code="file_size")
    if size == 0:
        raise ValidationError("The file is empty.", code="file_empty")
    head = uploaded.read(16)
    uploaded.seek(0)
    signatures = MAGIC.get(ext)
    if signatures and not any(head.startswith(sig) for sig in signatures):
        raise ValidationError("File content does not match its extension.", code="file_content")
    if ext in conf.get("CORE_IMAGE_EXTENSIONS"):
        try:
            from PIL import Image

            Image.open(uploaded).verify()
        except Exception as exc:  # noqa: BLE001
            raise ValidationError("Not a valid image.", code="image") from exc
        finally:
            uploaded.seek(0)
    return ext


def upload_to_random(instance, filename: str) -> str:
    """``upload_to`` callable for business FileFields: randomised name, original extension only, date-less flat directory."""
    return "uploads/" + random_storage_name(os.path.splitext(filename)[1])
