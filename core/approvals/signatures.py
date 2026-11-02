"""Signature boxes for printed documents, read from a record's approval history (latest request, current round).

``approval_signatures(obj)`` -> ``[Signature("Prepared By", submitter, submitted_at), Signature(<step>, approver, approved_at), ...]``
-- one box per chain step that was not skipped; steps not decided yet get an empty box (to sign by hand). A step passed with an
observation counts as signed. Each box carries the signer's signature image (Profile ``user_sign``) when there is one. Pass the
list as ``meta["signatures"]`` to a PDF, or return it in a custom report's ``ReportOutput.signatures``; record detail pages and
their PDF / Excel exports show it on their own.

Box headings: ``labels`` (by step code) win, then the step's *Signature label* (Approval steps page), then the step name.
"""
from __future__ import annotations

from core.models import Approval
from core.reports.letterhead import Signature, user_display_name

from .service import PASS_ACTIONS, latest_request

MAX_IMAGE_BYTES = 1_000_000


def signature_image(user) -> bytes | None:
    """The user's stored signature image (bytes), or None when there is none or it cannot be read."""
    profile = getattr(user, "profile", None) if getattr(user, "pk", None) else None
    stored = getattr(profile, "user_sign", None)
    if not stored:
        return None
    try:
        with stored.open("rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
    except Exception:  # noqa: BLE001 - a missing file must not break a page or a PDF
        return None
    return data if 0 < len(data) <= MAX_IMAGE_BYTES else None


def approval_signatures(obj, *, prepared_label: str = "Prepared By", labels: dict | None = None,
                        include_pending: bool = True, images: bool = True) -> list[Signature]:
    """``labels`` renames steps by code, e.g. ``{"merch": "Checked By", "manager": "Approved By"}``."""
    req = latest_request(obj)
    if req is None:
        return []
    labels = labels or {}
    history = list(req.history.filter(round=req.round).select_related("actor__profile").order_by("created_at", "pk"))
    submitted = next((h for h in reversed(history) if h.action in (Approval.SUBMIT, Approval.RESUBMIT)), None)
    preparer = submitted.actor if submitted else req.submitted_by

    def sign(label, user, when):
        return Signature(label, user_display_name(user), when, signature_image(user) if images else None)

    out = [sign(prepared_label, preparer, submitted.created_at if submitted else req.created_at)]
    skipped = {h.sequence for h in history if h.action == Approval.SKIP}
    passed = {h.sequence: h for h in history if h.action in PASS_ACTIONS}     # latest pass (approve / observation) per step
    for step in req.chain:
        if step["sequence"] in skipped or not step.get("enabled", True):
            continue
        label = labels.get(step["code"]) or step.get("signature_label") or step["name"]
        decision = passed.get(step["sequence"])
        if decision is not None:
            out.append(sign(label, decision.actor, decision.created_at))
        elif include_pending:
            out.append(Signature(label))
    return out


def record_signatures(obj) -> list[Signature]:
    """The signature row under a record's detail page and its PDF / Excel export: none when the record has no approval, was
    never submitted, or its request was withdrawn / cancelled."""
    from .errors import ApprovalError
    from .registry import target_for

    try:
        target_for(obj)
    except ApprovalError:
        return []
    req = latest_request(obj)
    if req is None or req.status == req.CANCELLED:
        return []
    return approval_signatures(obj)
