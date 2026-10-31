"""Orphan cleanup for private media: files not referenced by any FileField and older than the grace period."""
from __future__ import annotations

import os
import time
from pathlib import Path

from django.apps import apps
from django.db import models

from core import conf


def referenced_names() -> set[str]:
    names: set[str] = set()
    for model in apps.get_models():
        for f in model._meta.concrete_fields:
            if isinstance(f, models.FileField):
                for value in model._base_manager.exclude(**{f.name: ""}).exclude(**{f"{f.name}__isnull": True}).values_list(f.name, flat=True).iterator():
                    names.add(str(value))
    return names


def find_orphans(now: float | None = None) -> list[Path]:
    root = conf.private_media_root()
    if not root.is_dir():
        return []
    now = now or time.time()
    grace = conf.get("CORE_ORPHAN_GRACE_HOURS") * 3600
    keep = referenced_names()
    out = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel not in keep and now - path.stat().st_mtime >= grace:
            out.append(path)
    return out


def delete_orphans(dry_run: bool = True) -> list[str]:
    root = conf.private_media_root().resolve()
    removed = []
    for path in find_orphans():
        resolved = path.resolve()
        if root not in resolved.parents:        # path traversal / symlink escape guard
            continue
        removed.append(resolved.relative_to(root).as_posix())
        if not dry_run:
            os.unlink(resolved)
    return removed
