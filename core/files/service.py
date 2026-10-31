"""Private file store: bytes under CORE_PRIVATE_MEDIA_ROOT (outside any web-served directory), random names, authorised download."""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

from django.http import FileResponse, Http404
from django.utils import timezone

from core import conf
from core.files import validation


class PathEscape(Exception):
    pass


def root() -> Path:
    path = conf.private_media_root()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def safe_path(storage_name: str) -> Path:
    """Resolve a stored name inside the root; refuses separators, '..', absolute paths and symlinks pointing outside."""
    if not storage_name or "/" in storage_name or "\\" in storage_name or storage_name.startswith(".") or "\x00" in storage_name:
        raise PathEscape(storage_name)
    base = root()
    candidate = (base / storage_name).resolve()
    if base != candidate.parent:
        raise PathEscape(storage_name)
    return candidate


def save_upload(uploaded, *, user, company=None, target=None, allowed_extensions=None):
    from django.contrib.contenttypes.models import ContentType

    from core.models import StoredFile

    ext = validation.validate_upload(uploaded, allowed_extensions=allowed_extensions)
    storage_name = validation.random_storage_name(ext)
    digest = hashlib.sha256()
    dest = safe_path(storage_name)
    with open(dest, "xb") as handle:          # "x": never overwrite
        if hasattr(os, "fchmod"):             # POSIX only; on Windows the file inherits the folder's ACL
            os.fchmod(handle.fileno(), 0o600)
        for chunk in uploaded.chunks():
            digest.update(chunk)
            handle.write(chunk)
    kwargs = {}
    if target is not None:
        kwargs = dict(target_ct=ContentType.objects.get_for_model(target), target_id=str(target.pk), attached=True)
    return StoredFile.objects.create(
        company=company, storage_name=storage_name, original_name=validation.sanitize_name(uploaded.name),
        content_type_header=(getattr(uploaded, "content_type", "") or "")[:100], size=uploaded.size, sha256=digest.hexdigest(),
        created_by=user, **kwargs)


def can_download(user, stored) -> bool:
    from core.permissions.service import user_has_perm

    if not user.is_authenticated:
        return False
    if stored.target_ct_id:
        model = stored.target_ct.model_class()
        target = model._default_manager.filter(pk=stored.target_id).first() if model else None
        if target is None:
            return False
        return user_has_perm(user, f"{model._meta.app_label}.view_{model._meta.model_name}", target)
    if user.is_superuser:
        return True
    if stored.company_id:
        from core.permissions.scope import user_company_ids

        return stored.created_by_id == user.pk and stored.company_id in user_company_ids(user)
    return stored.created_by_id == user.pk


def response_for(user, stored):
    if not can_download(user, stored):
        raise Http404("Not found")      # 404, not 403: do not confirm the file exists
    try:
        path = safe_path(stored.storage_name)
        handle = open(path, "rb")
    except (PathEscape, FileNotFoundError):
        raise Http404("Not found")
    response = FileResponse(handle, as_attachment=True, filename=stored.original_name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def cleanup_orphans(*, dry_run: bool = False) -> dict:
    """Delete StoredFile rows (and bytes) never attached within the grace period, plus bytes with no row."""
    from core.models import StoredFile

    cutoff = timezone.now() - timedelta(hours=conf.get("CORE_ORPHAN_GRACE_HOURS"))
    removed_rows = removed_files = 0
    for stored in StoredFile.objects.filter(attached=False, created_at__lt=cutoff):
        if not dry_run:
            try:
                safe_path(stored.storage_name).unlink(missing_ok=True)
            except PathEscape:
                pass
            stored.delete()
        removed_rows += 1
    known = set(StoredFile.objects.values_list("storage_name", flat=True))
    for entry in root().iterdir():
        if entry.is_file() and not entry.is_symlink() and entry.name not in known and not entry.name.startswith("exports_") and \
                entry.stat().st_mtime < cutoff.timestamp() and len(entry.name) >= 32:
            if not dry_run:
                entry.unlink()
            removed_files += 1
    return {"rows": removed_rows, "files": removed_files, "dry_run": dry_run}
