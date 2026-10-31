"""Pure helpers: never let secrets, hashes, tokens or file contents reach the audit log or the application log."""
from __future__ import annotations

import re
from typing import Any

MASK = "[redacted]"
_SENSITIVE = re.compile(r"(pass(word|wd)?|secret|token|hash|api[_-]?key|authorization|cookie|session|csrf|otp|signature|"
                        r"credential|private[_-]?key|salt)", re.I)
MAX_VALUE_LENGTH = 500
MAX_LIST_ITEMS = 1000


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE.search(name or ""))


def clean_value(value: Any) -> Any:
    """JSON-safe, bounded representation of a field value. Bytes and file contents are never stored."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    text = str(value)
    return text if len(text) <= MAX_VALUE_LENGTH else text[:MAX_VALUE_LENGTH] + "..."


def redact_mapping(data: dict, extra_exclude=()) -> dict:
    """Copy of ``data`` with sensitive keys masked and every value cleaned (recursively for dict/list)."""
    out: dict = {}
    for key, value in data.items():
        if key in extra_exclude or is_sensitive_name(str(key)):
            out[key] = MASK
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, extra_exclude)
        elif isinstance(value, (list, tuple)):
            out[key] = _clean_list(value, extra_exclude)
        else:
            out[key] = clean_value(value)
    return out


def _clean_list(value, extra_exclude=()) -> list:
    return [redact_mapping(v, extra_exclude) if isinstance(v, dict) else _clean_list(v, extra_exclude) if isinstance(v, (list, tuple))
            else clean_value(v) for v in value][:MAX_LIST_ITEMS]


def diff_snapshots(old: dict | None, new: dict | None) -> dict:
    """``{field: [old, new]}`` for fields that changed (all fields for create/delete). Values are already cleaned."""
    old, new = old or {}, new or {}
    changes = {}
    for field in sorted(set(old) | set(new)):
        if old.get(field) != new.get(field):
            changes[field] = [old.get(field), new.get(field)]
    return changes
