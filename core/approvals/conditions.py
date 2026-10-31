"""Step conditions ("only when"): a step runs only for records that match a filter, otherwise it is skipped and logged.

Thin wrapper over :mod:`core.search.conditions` (shared with notification rules) that reports problems as :class:`ApprovalError`.
"""
from __future__ import annotations

from core.search import conditions as shared
from core.search.errors import SearchError

from .errors import ApprovalError

EMPTY = shared.EMPTY


def normalize(raw) -> dict:
    try:
        return shared.normalize(raw)
    except SearchError as exc:
        raise ApprovalError(str(exc)) from None


def validate(model, raw) -> dict:
    try:
        return shared.validate(model, raw)
    except SearchError as exc:
        raise ApprovalError(f"Step condition: {exc}") from None


def matches(obj, tree: dict) -> bool:
    try:
        return shared.matches(obj, tree)
    except SearchError as exc:
        raise ApprovalError(f"Step condition: {exc}") from None
