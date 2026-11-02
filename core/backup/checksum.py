"""Checksum helpers (re-exported so callers import one obvious place)."""
from .naming import sha256_file

__all__ = ["sha256_file", "verify_sha256"]


def verify_sha256(path, expected: str) -> bool:
    return bool(expected) and sha256_file(path) == expected.lower()
