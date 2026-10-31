"""Server-generated, collision-free file names and safe path joining. Pure Python.

File names are never taken from user input: they are built from the backup kind/mode/engine, a UTC timestamp and a random
token, and the file is reserved with O_CREAT|O_EXCL so an existing backup can never be overwritten, not even by a race.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

_SAFE = re.compile(r"[^a-z0-9]+")
SIDECAR_SUFFIX = ".json"


class UnsafePath(ValueError):
    pass


def slug(text: str) -> str:
    return _SAFE.sub("-", text.lower()).strip("-") or "x"


def reserve_unique_file(directory: Path, prefix: str, extension: str, now: dt.datetime | None = None) -> Path:
    """Create an empty 0600 file ``<prefix>-<UTC timestamp>-<random>.<ext>`` in ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(20):
        path = directory / f"{slug(prefix)}-{stamp}-{secrets.token_hex(4)}{extension}"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return path
        except FileExistsError:
            continue
    raise OSError("Could not reserve a unique backup file name")


def reserve_unique_dir(parent: Path, prefix: str, now: dt.datetime | None = None) -> Path:
    """Create a NEW empty directory under ``parent`` (restores never write into an existing directory)."""
    parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(20):
        path = parent / f"{slug(prefix)}-{stamp}-{secrets.token_hex(4)}"
        try:
            path.mkdir(mode=0o700)
            return path
        except FileExistsError:
            continue
    raise OSError("Could not create a unique restore directory")


def safe_join(base: Path, name: str) -> Path:
    """``base/name`` resolved, guaranteed to be strictly inside ``base`` (no ``..``, absolute names or symlink escapes)."""
    if not name or "\x00" in name or Path(name).name != name:
        raise UnsafePath(f"Unsafe file name: {name!r}")
    base_resolved = base.resolve(strict=False)
    candidate = (base_resolved / name).resolve(strict=False)
    if candidate == base_resolved or not candidate.is_relative_to(base_resolved):
        raise UnsafePath(f"Path escapes the backup directory: {name!r}")
    return candidate


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def write_sidecar(data_file: Path, metadata: dict) -> Path:
    """Write ``<file>.json`` next to the backup (atomic replace). Metadata must already be free of secrets."""
    target = data_file.with_name(data_file.name + SIDECAR_SUFFIX)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return target
