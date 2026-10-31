"""Per-request context (current user / active companies / request id) held in ``contextvars`` so models and signals can stamp
``created_by`` / ``deleted_by`` and write audit rows without a request object."""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager

_user: ContextVar = ContextVar("core_user", default=None)
_ip: ContextVar = ContextVar("core_ip", default="")
_company_ids: ContextVar = ContextVar("core_company_ids", default=None)
_active_company: ContextVar = ContextVar("core_active_company", default=None)
_audit_suppressed: ContextVar = ContextVar("core_audit_suppressed", default=False)


def set_context(user=None, ip: str = "", company_ids=None, active_company=None):
    return (_user.set(user), _ip.set(ip or ""), _company_ids.set(company_ids), _active_company.set(active_company))


def reset_context(tokens) -> None:
    for var, token in zip((_user, _ip, _company_ids, _active_company), tokens):
        var.reset(token)


def current_user():
    user = _user.get()
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def current_ip() -> str:
    return _ip.get()


def current_company_ids():
    return _company_ids.get()


def current_company():
    return _active_company.get()


@contextmanager
def acting_as(user, ip: str = "", company_ids=None, active_company=None):
    tokens = set_context(user, ip, company_ids, active_company)
    try:
        yield
    finally:
        reset_context(tokens)


@contextmanager
def audit_suppressed():
    token = _audit_suppressed.set(True)
    try:
        yield
    finally:
        _audit_suppressed.reset(token)


def is_audit_suppressed() -> bool:
    return _audit_suppressed.get()
