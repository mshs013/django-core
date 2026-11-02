"""Identifier validation and quoting. Pure Python.

Table and column names reach SQL text (they cannot be bound as parameters), so they are validated against a strict
whitelist first and quoted second. A name that does not match is rejected instead of escaped: schemas created by this
project never need exotic names, and refusing is safer than clever escaping.
"""
from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
MAX_IDENTIFIER_LENGTH = 64


class InvalidIdentifier(ValueError):
    pass


def validate_identifier(name: str) -> str:
    if not isinstance(name, str) or not _IDENTIFIER.match(name) or len(name) > MAX_IDENTIFIER_LENGTH:
        raise InvalidIdentifier(f"Unsafe or invalid SQL identifier: {name!r}")
    return name


def quote_double(name: str) -> str:
    """PostgreSQL style: "name"."""
    return '"' + validate_identifier(name) + '"'
