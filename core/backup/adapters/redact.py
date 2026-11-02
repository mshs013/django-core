"""Secret redaction for anything that is stored, logged or shown (errors, URLs, command lines). Pure Python.

Two layers: exact secrets the caller knows (the connection password) are replaced wherever they occur, and generic
patterns catch credentials we were not told about (``scheme://user:pw@host``, ``password=...``, ``PGPASSWORD=...``).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "***"
MAX_LENGTH = 4000

_URL_PASSWORD = re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)")
_KEY_VALUE = re.compile(r"(?i)\b((?:pg)?pass(?:word|wd)?|pwd|secret|token)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_PASSWORD_FLAG = re.compile(r"(?i)(--password(?:=|\s+))(\S+)")


def redact(text: object, secrets: Iterable[str | None] = (), limit: int = MAX_LENGTH) -> str:
    """Return ``text`` as a string with every known secret and every recognisable credential masked, truncated to ``limit``."""
    if text is None:
        return ""
    out = str(text)
    # Longest first so a secret that contains another secret is masked completely.
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, MASK)
    out = _URL_PASSWORD.sub(rf"\1{MASK}\3", out)
    out = _PASSWORD_FLAG.sub(rf"\1{MASK}", out)
    out = _KEY_VALUE.sub(rf"\1\2{MASK}", out)
    return out[:limit]
