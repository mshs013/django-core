"""Approval engine service API. Every function is atomic, authorises through ``core.permissions`` (fail closed, company aware,
object aware) and raises :class:`ApprovalError` with a user-friendly message.

Concurrency: decisions lock the request row (and the target row) with ``select_for_update`` inside ``transaction.atomic()``;
the ``expected_sequence`` argument guards against stale forms (someone else already decided that step).
Notifications are scheduled with ``transaction.on_commit`` (via ``core.notifications.notify``).
"""
from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core import conf
from core.audit.service import record_event
from core.models import Approval, ApprovalRequest, ApprovalType, ApprovalTypeStep
from core.models.approvals import CancelableModel
from core.notifications.service import notify, superusers
from core.signals import approval_event
from core.permissions import user_company_ids, user_has_perm

from . import conditions
from .errors import ApprovalError
from .registry import CHAINS, _record_company, get_skip_rule, has_approval, sync_chain, target_for

log = logging.getLogger("core.approvals")
SEQ_STEP = 1000
PASS_ACTIONS = (Approval.APPROVE, Approval.OBSERVE)              # history actions that passed a step


def _ct(obj):
    return ContentType.objects.get_for_model(obj, for_concrete_model=False)


def _company_id(obj):
    return _record_company(obj)


def _history(req: ApprovalRequest, action: str, actor, step: dict | None = None, comment: str = "") -> None:
    Approval.objects.create(request=req, round=req.round, sequence=(step or {}).get("sequence", 0), step_name=(step or {}).get("name", ""),
                            action=action, actor=actor if getattr(actor, "pk", None) else None, comment=comment[:2000])
    record_event("approval", message=f"{action}: {req.object_repr} [{(step or {}).get('name', '')}]"[:480], company=req.company_id,
                 model_label=req.content_type.model_class()._meta.label if req.content_type.model_class() else "", object_pk=req.object_id,
                 object_repr=req.object_repr, user=actor if getattr(actor, "pk", None) else None)


# --------------------------------------------------------------------------------------------------------------- chain snapshot
def _user_ids(step: dict) -> list[int]:
    """Approver user ids of a chain step: ``approver_user_ids`` (list), or the single ``approver_user_id`` of requests submitted
    before steps could have several approvers."""
    ids = step.get("approver_user_ids")
    if ids is None:
        ids = [step["approver_user_id"]] if step.get("approver_user_id") else []
    return sorted({int(i) for i in ids if i})


def _extra_defaults(extra: dict) -> dict:
    """An extra step from its caller's dict. Without a code one is made from the name (kept unique inside the chain)."""
    from django.utils.text import slugify

    code, name = str(extra.get("code", "")).strip(), str(extra.get("name", "")).strip()
    if not name:
        raise ApprovalError("An extra step needs a name.")
    return {"code": (code or slugify(name) or "step")[:60], "auto_code": not code, "name": name[:120],
            "permission": str(extra.get("permission", "") or ""), "approver_user_ids": _user_ids(extra),
            "approver_group_id": extra.get("approver_group_id"), "skip_rule": str(extra.get("skip_rule", "") or ""),
            "after": extra.get("after")}


def build_chain(atype: ApprovalType, extra_steps=None, model=None) -> list[dict]:
    rows = list(atype.type_steps.select_related("step").prefetch_related("approver_users").order_by("position", "pk"))
    if not rows:
        raise ApprovalError("This approval type has no steps.")
    finals = [r for r in rows if r.is_final and r.is_enabled]
    if len(finals) != 1:
        raise ApprovalError("This approval chain must have exactly one final step.")
    chain = []
    for index, r in enumerate(rows, start=1):
        chain.append({"sequence": index * SEQ_STEP, "code": r.step.code, "name": r.step.name, "permission": r.permission or r.step.permission,
                      "skip_rule": r.step.skip_rule, "enabled": bool(r.is_enabled and r.step.is_active), "is_final": r.is_final,
                      "approver_user_ids": sorted(u.pk for u in r.approver_users.all()), "approver_group_id": r.approver_group_id,
                      "kind": "chain", **step_labels(r.step),
                      "condition": conditions.validate(model, r.condition) if r.condition else conditions.normalize(None)})
    for extra in extra_steps or []:
        _insert_extra(chain, _extra_defaults(extra))
    _validate_chain(chain)
    for s in chain:
        s.pop("_old", None)
    return chain


def step_labels(step) -> dict:
    """How a step is shown to approvers; copied into the frozen chain so later edits never change a running request."""
    return {"display_name": step.display_name or step.name, "pass_label": step.pass_label or "Approve",
            "fail_label": step.fail_label or "Reject", "button_text": step.button_text or "Submit",
            "allow_observation": bool(step.allow_observation), "signature_label": step.signature_label or ""}


def step_ui(step: dict | None) -> dict:
    """Labels of a chain step, with defaults for requests submitted before the labels existed (and for extra steps)."""
    step = step or {}
    return {"display_name": step.get("display_name") or step.get("name", ""), "pass_label": step.get("pass_label") or "Approve",
            "fail_label": step.get("fail_label") or "Reject", "button_text": step.get("button_text") or "Submit",
            "allow_observation": bool(step.get("allow_observation"))}


def _validate_chain(chain: list[dict]) -> None:
    seqs = [s["sequence"] for s in chain]
    codes = [s["code"] for s in chain]
    if len(set(seqs)) != len(seqs) or seqs != sorted(seqs):
        raise ApprovalError("Approval step order is invalid.")
    if len(set(codes)) != len(codes):
        raise ApprovalError("That step is already part of this approval.")
    finals = [s for s in chain if s["is_final"]]
    if len(finals) != 1 or chain[-1] is not finals[0]:
        raise ApprovalError("An approval needs exactly one final step, and it must be last.")
    if finals[0]["skip_rule"] or not finals[0]["enabled"] or (finals[0].get("condition") or {}).get("items"):
        raise ApprovalError("The final step cannot be disabled, auto-skipped or conditional.")
    for s in chain:
        if s["skip_rule"]:
            get_skip_rule(s["skip_rule"])                     # unknown rule -> ApprovalError at submit time, not mid-flow


def _insert_extra(chain: list[dict], extra: dict, after_sequence: int | None = None, before_sequence: int | None = None) -> dict:
    final_index = next(i for i, s in enumerate(chain) if s["is_final"])
    if before_sequence is not None:
        index = next((i for i, s in enumerate(chain) if s["sequence"] == before_sequence), None)
        if index is None:
            raise ApprovalError("The step to insert before was not found.")
        return _put_extra(chain, extra, index)
    if after_sequence is None and extra.get("after"):
        match = next((s for s in chain if s["code"] == extra["after"]), None)
        after_sequence = match["sequence"] if match else None
    if after_sequence is None:
        index = final_index                                    # default: right before the final step
    else:
        pos = next((i for i, s in enumerate(chain) if s["sequence"] == after_sequence), None)
        if pos is None:
            raise ApprovalError("The step to insert after was not found.")
        if pos >= final_index:
            raise ApprovalError("A step cannot be inserted after the final step.")
        index = pos + 1
    return _put_extra(chain, extra, index)


def _put_extra(chain: list[dict], extra: dict, index: int) -> dict:
    lo = chain[index - 1]["sequence"] if index > 0 else 0
    hi = chain[index]["sequence"]
    if hi - lo < 2:                                            # no gap: respace (callers remap current_sequence)
        for n, s in enumerate(chain, start=1):
            s["_old"] = s["sequence"]
            s["sequence"] = n * SEQ_STEP
        lo = chain[index - 1]["sequence"] if index > 0 else 0
        hi = chain[index]["sequence"]
    code, taken, n = extra["code"], {s["code"] for s in chain}, 1
    while extra.get("auto_code") and code in taken:            # "legal", "legal-2", ... for steps named alike
        n += 1
        code = f"{extra['code'][:55]}-{n}"
    step = {"sequence": (lo + hi) // 2, "code": code, "name": extra["name"], "permission": extra["permission"],
            "skip_rule": extra["skip_rule"], "enabled": True, "is_final": False, "approver_user_ids": extra["approver_user_ids"],
            "approver_group_id": extra["approver_group_id"], "kind": "extra"}
    chain.insert(index, step)
    return step


# --------------------------------------------------------------------------------------------------------------- authorisation
def _lock_target(req: ApprovalRequest):
    model = req.content_type.model_class()
    if model is None:
        raise ApprovalError("The record behind this approval no longer exists.")
    try:
        return model._base_manager.select_for_update().get(pk=req.object_id)
    except model.DoesNotExist:
        raise ApprovalError("The record behind this approval no longer exists.") from None


def _target(req: ApprovalRequest):
    model = req.content_type.model_class()
    return model._base_manager.filter(pk=req.object_id).first() if model else None


def _view_perm(obj) -> str:
    return f"{obj._meta.app_label}.view_{obj._meta.model_name}"


def _member(user, company_id) -> bool:
    return company_id is None or company_id in user_company_ids(user)


def _named(user, step: dict) -> bool:
    """Is ``user`` named on the step, directly (approver users) or through its approver group?"""
    if user.pk in _user_ids(step):
        return True
    return bool(step.get("approver_group_id")) and user.groups.filter(pk=step["approver_group_id"]).exists()


def _step_allows(user, step: dict, req: ApprovalRequest, obj) -> bool:
    if user.is_superuser or _named(user, step):
        return True
    perm = step.get("permission")
    return bool(perm) and user_has_perm(user, perm, obj, req.company_id)


def _may_decide(user, req: ApprovalRequest, step: dict, obj) -> bool:
    """The one rule for "may decide this step": used by :func:`can_act`, the queue and the "Approval needed" notice, so whoever is
    told to approve always finds the request in their queue (and nobody is told who cannot act)."""
    if not user.is_active or not _member(user, req.company_id):
        return False
    if req.submitted_by_id == user.pk and not conf.get("CORE_SELF_APPROVAL"):
        return False                                           # self-approval is off by default (superusers included)
    atype = req.approval_type
    if atype.company_id and atype.company_id != req.company_id:
        return False
    if atype.permission and not user_has_perm(user, atype.permission, obj, req.company_id):
        return False
    if not _named(user, step) and not user_has_perm(user, _view_perm(obj), obj):
        return False                                           # must see the record, unless picked for this step by name
    return _step_allows(user, step, req, obj)


def can_act(user, req: ApprovalRequest) -> bool:
    """May ``user`` decide the request's current step right now? (False on anything doubtful.)"""
    if not getattr(user, "is_authenticated", False) or not user.is_active or req.status != ApprovalRequest.PENDING:
        return False
    step = req.step_at(req.current_sequence)
    if step is None:
        return False
    obj = _target(req)
    if obj is None:
        return False
    return _may_decide(user, req, step, obj)


def can_view(user, req: ApprovalRequest) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active or not _member(user, req.company_id):
        return False
    obj = _target(req)
    if obj is None:
        return False
    if user.is_superuser or req.submitted_by_id == user.pk:
        return user_has_perm(user, _view_perm(obj), obj)
    named = any(_named(user, s) for s in req.chain)
    if not named and not user_has_perm(user, _view_perm(obj), obj):
        return False
    atype = req.approval_type
    if atype.permission and not user_has_perm(user, atype.permission, obj, req.company_id):
        return False
    return named or _adds_steps(user, req) or any(_step_allows(user, s, req, obj) for s in req.chain)


def _adds_steps(user, req: ApprovalRequest) -> bool:
    return (user.is_superuser or user_has_perm(user, "core.add_step_to_request")
            or req.approval_type.step_adders.filter(pk=user.pk).exists())


def can_cancel(user, obj) -> bool:
    """CancelableModel rows: cancel the record (also after approval). Other approvable records: withdraw a pending request."""
    if not isinstance(obj, CancelableModel):
        try:
            cfg = target_for(obj)
        except ApprovalError:
            return False
        req = latest_request(obj)                      # withdraw: only the person who submitted the pending request
        return (getattr(user, "is_authenticated", False) and user.is_active and _member(user, _company_id(obj))
                and req is not None and req.status == ApprovalRequest.PENDING and req.submitted_by_id == user.pk
                and _may_submit(user, obj, cfg))
    if obj.is_cancel:
        return False
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if not _member(user, _company_id(obj)):
        return False
    return user_has_perm(user, f"{obj._meta.app_label}.change_{obj._meta.model_name}", obj)


def latest_request(obj) -> ApprovalRequest | None:
    return (ApprovalRequest.objects.filter(content_type=_ct(obj), object_id=str(obj.pk)).select_related("approval_type", "content_type")
            .order_by("-created_at", "-pk").first())


def status_for(obj) -> str:
    """``none`` | ``pending`` | ``approved`` | ``rejected`` | ``cancelled``"""
    req = latest_request(obj)
    return req.status if req else "none"


def is_locked(obj) -> bool:
    try:
        cfg = target_for(obj)
    except ApprovalError:
        return False
    status = status_for(obj)
    return status in cfg.lock_statuses


def _may_submit(user, obj, cfg) -> bool:
    """``submit_permission`` when the target sets one; otherwise whoever may create or change the record may send it."""
    if cfg.submit_permission:
        return user_has_perm(user, cfg.submit_permission, obj)
    meta = obj._meta
    return any(user_has_perm(user, f"{meta.app_label}.{action}_{meta.model_name}", obj) for action in ("change", "add"))


def can_submit(user, obj) -> bool:
    try:
        cfg = target_for(obj)
    except ApprovalError:
        return False
    if not getattr(user, "is_authenticated", False) or not user.is_active or not _member(user, _company_id(obj)):
        return False
    if getattr(obj, "is_cancel", False) or status_for(obj) in ("pending", "approved"):
        return False
    return _may_submit(user, obj, cfg)


# --------------------------------------------------------------------------------------------------------------- notifications
def _approvers(req: ApprovalRequest, step: dict, obj):
    """People to tell about a step: everyone who may decide it (superusers only when named, or when nobody else can)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    candidates = User.objects.filter(is_active=True, profile__companies=req.company_id) if req.company_id else User.objects.filter(is_active=True)
    deciders = [u for u in candidates.distinct()[:500] if _may_decide(u, req, step, obj)]
    allowed = [u for u in deciders if not u.is_superuser or _named(u, step)]
    if not allowed:
        allowed = [u for u in superusers() if _may_decide(u, req, step, obj)]
    return allowed


def _notify_step(req: ApprovalRequest, step: dict, obj, cfg, extra: str = "") -> None:
    people = _approvers(req, step, obj)
    if people:
        notify(people, f"Approval needed: {req.object_repr}", body=f"{step['name']}{extra}", url=_approval_url(req),
               level="warning", kind="approval", company=req.company_id)
    else:                                                      # nobody can decide: say so instead of silently waiting forever
        log.warning("approval request %s: nobody can decide step %r", req.pk, step.get("name"))
        _notify_submitter(req, f"No approver for: {req.object_repr}",
                          f"Nobody can decide the step \"{step['name']}\" yet. Ask an administrator to add an approver.", obj, cfg,
                          level="danger")


def _approval_url(req: ApprovalRequest) -> str:
    from django.urls import reverse

    return reverse("core:approval_detail", args=[req.pk])


def _notify_submitter(req: ApprovalRequest, title: str, body: str, obj, cfg, level: str = "info", email: bool | None = None) -> None:
    if req.submitted_by_id:
        notify([req.submitted_by], title, body=body, url=cfg.url(obj) or "", level=level, kind="approval", company=req.company_id,
               email=email)


def _notify_finished(req: ApprovalRequest, outcome: str, title: str, body: str, obj, cfg, level: str) -> None:
    """Final approve/reject: the submitter always, plus the users/groups set on the approval type (members of the record's
    company only), by e-mail as well when the type asks for it."""
    atype = req.approval_type
    email = True if atype.notify_email else None
    _notify_submitter(req, title, body, obj, cfg, level, email=email)
    if not atype.notifies(outcome):
        return
    from django.contrib.auth import get_user_model

    people = get_user_model().objects.filter(is_active=True).filter(
        Q(pk__in=atype.notify_users.values("pk")) | Q(groups__in=atype.notify_groups.all()))
    if req.company_id:
        people = people.filter(profile__companies=req.company_id)
    recipients = [u for u in people.distinct() if u.pk != req.submitted_by_id]
    notify(recipients, title, body=body, url=cfg.url(obj) or "", level=level, kind="approval", company=req.company_id, email=email)


# --------------------------------------------------------------------------------------------------------------- flow
def _advance(req: ApprovalRequest, obj, from_sequence: int, actor) -> dict:
    """Move to the next step after ``from_sequence``, logging (and passing over) disabled / auto-skipped steps."""
    for step in req.chain:
        if step["sequence"] <= from_sequence:
            continue
        if not step["enabled"]:
            _history(req, Approval.SKIP, actor, step, "Step disabled")
            continue
        if step["skip_rule"] and get_skip_rule(step["skip_rule"])(obj, req):
            _history(req, Approval.SKIP, actor, step, f"Auto-skipped by rule {step['skip_rule']}")
            continue
        if not conditions.matches(obj, step.get("condition") or {}):
            _history(req, Approval.SKIP, actor, step, "Skipped: the record does not match the step condition")
            continue
        req.current_sequence = step["sequence"]
        return step
    raise ApprovalError("The approval chain has no remaining step.")


def _set_flags(obj, *, confirm: bool, approved: bool) -> None:
    """Mirror the state onto ``AuditedModel`` flags. Models approved through the app may not have them: their state is the request."""
    values = {"is_confirm": confirm, "is_approval": approved, "approved_at": timezone.now() if approved else None}
    names = {f.name for f in obj._meta.concrete_fields}
    values = {k: v for k, v in values.items() if k in names}
    for name, value in values.items():
        setattr(obj, name, value)
    if values:
        from core.audit.service import audited_update

        audited_update(type(obj)._base_manager.filter(pk=obj.pk), message="approval state", **values)


def _copy_flags(obj, source) -> None:
    for name in ("is_confirm", "is_approval", "approved_at"):
        if hasattr(source, name):
            setattr(obj, name, getattr(source, name))


def _announce(obj, req: ApprovalRequest, action: str, actor) -> None:
    """``core.signals.approval_event`` for other apps and notification rules: submitted | approved | rejected."""
    approval_event.send(sender=type(obj), instance=obj, request=req, action=action, actor=actor)


def _approval_type(cfg) -> ApprovalType:
    """The DB chain for a target. Code chains get their rows created on first use; after that the rows are the truth."""
    atype = ApprovalType.objects.filter(code=cfg.chain).first()
    if atype is None or not atype.type_steps.exists():
        if cfg.chain not in CHAINS:
            raise ApprovalError("The approval chain for this record is not configured.")
        atype = sync_chain(cfg.chain)
    return atype


def submit_for_approval(user, obj, *, extra_steps=None, comment: str = "") -> ApprovalRequest:
    cfg = target_for(obj)
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        raise ApprovalError("Sign in to continue.")
    if not _member(user, _company_id(obj)):
        raise ApprovalError("You cannot submit records of another company.")
    if not _may_submit(user, obj, cfg):
        raise ApprovalError("You do not have permission to send this for approval.")
    try:
        with transaction.atomic():
            locked = type(obj)._base_manager.select_for_update().get(pk=obj.pk)
            if getattr(locked, "is_cancel", False):
                raise ApprovalError("A cancelled record cannot be sent for approval.")
            previous = latest_request(locked)
            if previous is not None and previous.status == ApprovalRequest.PENDING:
                raise ApprovalError("This is already waiting for approval.")
            if previous is not None and previous.status == ApprovalRequest.APPROVED and getattr(locked, "is_approval", True):
                raise ApprovalError("This is already approved.")
            if previous is not None and previous.status == ApprovalRequest.REJECTED:
                return resubmit(user, locked, comment=comment, extra_steps=extra_steps)
            atype = _approval_type(cfg)
            if not atype.is_active:
                raise ApprovalError("This approval type is switched off.")
            company_id = _company_id(locked)
            if atype.company_id and atype.company_id != company_id:
                raise ApprovalError("This approval type is not available for the record's company.")
            chain = build_chain(atype, extra_steps, type(locked))
            req = ApprovalRequest(approval_type=atype, target_key=cfg.key, content_type=_ct(locked), object_id=str(locked.pk),
                                  object_repr=cfg.title(locked)[:300], company_id=company_id, chain=chain,
                                  extra_steps=[s for s in chain if s["kind"] == "extra"], reject_mode=atype.reject_mode,
                                  submitted_by=user, round=1)
            req.save()
            _history(req, Approval.SUBMIT, user, None, comment)
            for s in req.extra_steps:
                _history(req, Approval.ADD_STEP, user, s, "Added at submission")
            step = _advance(req, locked, 0, user)
            req.save()
            _set_flags(locked, confirm=True, approved=False)
            _copy_flags(obj, locked)
            _notify_step(req, step, locked, cfg)
            _announce(locked, req, "submitted", user)
            return req
    except IntegrityError:
        raise ApprovalError("This is already waiting for approval.") from None


def resubmit(user, obj, *, comment: str = "", extra_steps=None) -> ApprovalRequest:
    cfg = target_for(obj)
    if not _may_submit(user, obj, cfg) or not _member(user, _company_id(obj)):
        raise ApprovalError("You do not have permission to resubmit this.")
    with transaction.atomic():
        locked = type(obj)._base_manager.select_for_update().get(pk=obj.pk)
        req = (ApprovalRequest.objects.select_for_update().filter(content_type=_ct(locked), object_id=str(locked.pk))
               .order_by("-created_at", "-pk").first())
        if req is None or req.status != ApprovalRequest.REJECTED:
            raise ApprovalError("Only a rejected request can be resubmitted.")
        if getattr(locked, "is_cancel", False):
            raise ApprovalError("A cancelled record cannot be resubmitted.")
        for extra in extra_steps or []:
            _insert_extra(req.chain, _extra_defaults(extra))
            _validate_chain(req.chain)
        req.round += 1
        req.status = ApprovalRequest.PENDING
        req.completed_at = None
        req.submitted_by = user
        req.object_repr = cfg.title(locked)[:300]
        req.current_sequence = 0
        req.save()
        _history(req, Approval.RESUBMIT, user, None, comment)
        step = _advance(req, locked, 0, user)
        req.save()
        _set_flags(locked, confirm=True, approved=False)
        _copy_flags(obj, locked)
        _notify_step(req, step, locked, cfg, " (resubmitted)")
        _announce(locked, req, "submitted", user)
        return req


def submit_approval_step(user, request_id: int, decision: str, *, comment: str = "", expected_sequence: int | None = None) -> ApprovalRequest:
    """Decide the current step. ``decision`` is ``"approve"``, ``"reject"`` or ``"observe"`` (a comment is required for the last
    two). An observation (only on steps that offer it) mails the record's creator and passes the step like an approval."""
    if decision not in ("approve", "reject", "observe"):
        raise ApprovalError("Unknown decision.")
    comment = (comment or "").strip()
    if decision == "reject" and not comment:
        raise ApprovalError("Please explain the rejection in a comment.")
    if decision == "observe" and not comment:
        raise ApprovalError("Please write the observation in the comment.")
    with transaction.atomic():
        try:
            req = ApprovalRequest.objects.select_for_update().select_related("approval_type", "content_type").get(pk=request_id)
        except ApprovalRequest.DoesNotExist:
            raise ApprovalError("That approval request does not exist.") from None
        if not _member(user, req.company_id):
            raise ApprovalError("That approval request does not exist.")           # no cross-company probing
        obj = _lock_target(req)
        cfg = target_for(obj)
        if req.status != ApprovalRequest.PENDING:
            raise ApprovalError("This request is no longer waiting for a decision.")
        if expected_sequence is not None and int(expected_sequence) != req.current_sequence:
            raise ApprovalError("This request changed while you were looking at it. Reload and try again.")
        if not can_act(user, req):
            raise ApprovalError("You are not allowed to decide this step.")
        step = req.step_at(req.current_sequence)
        if decision == "observe":
            if not step_ui(step)["allow_observation"]:
                raise ApprovalError("This step does not take observations.")
            _history(req, Approval.OBSERVE, user, step, comment)
            _send_observation(req, obj, user, comment)
            _pass_step(req, obj, cfg, user, step, comment)
        elif decision == "approve":
            _history(req, Approval.APPROVE, user, step, comment)
            _pass_step(req, obj, cfg, user, step, comment)
        else:
            _reject(req, obj, cfg, user, step, comment)
        return req


def _pass_step(req: ApprovalRequest, obj, cfg, user, step: dict, comment: str) -> None:
    """The step is passed (approved, or approved with an observation): open the next step, or finish the request."""
    if step["is_final"]:
        req.status, req.completed_at, req.current_sequence = ApprovalRequest.APPROVED, timezone.now(), 0
        req.save()
        _set_flags(obj, confirm=True, approved=True)
        if cfg.on_approved:
            cfg.on_approved(obj, req)
        _notify_finished(req, ApprovalType.NOTIFY_APPROVED, f"Approved: {req.object_repr}", comment, obj, cfg, "success")
        _announce(obj, req, "approved", user)
    else:
        nxt = _advance(req, obj, step["sequence"], user)
        req.save()
        _notify_step(req, nxt, obj, cfg)


def reference_of(req: ApprovalRequest, obj) -> tuple[str, str]:
    """(label, value) naming the record in observation mails: the type's reference column, else the record name."""
    name = req.approval_type.reference_field
    if obj is not None and name:
        try:
            field = obj._meta.get_field(name)
            return str(field.verbose_name), str(getattr(obj, name, "") or "")
        except Exception:                               # field renamed since the type was saved
            pass
    return "", req.object_repr or str(req.object_id)


def _creator(req: ApprovalRequest, obj):
    """The record's creator (``created_by``), else whoever submitted the request; None when that account is gone or inactive."""
    from django.contrib.auth import get_user_model

    uid = getattr(obj, "created_by_id", None) or req.submitted_by_id
    return get_user_model().objects.filter(pk=uid, is_active=True).first() if uid else None


def _mail(sender, people, subject: str, body: str, url: str, company=None) -> None:
    """Internal mail from ``sender`` (as the system when the sender may not write to them), + e-mail when e-mail notices are on."""
    from core.mail.service import MailError, send, send_system

    people = [u for u in people if u is not None and u.is_active]
    if not people:
        return
    try:
        send(sender, to=people, subject=subject, body=body, url=url, company=company)
    except MailError:
        send_system(people, subject, body, url, company=company)


def _send_observation(req: ApprovalRequest, obj, user, comment: str) -> None:
    """Internal mail (+ bell, + HTML e-mail when e-mail notices are on) to the record's creator, else the submitter."""
    from django.utils.html import escape

    from core.mail.service import MailError, send, send_system

    creator = _creator(req, obj)
    if creator is None:
        return
    label, value = reference_of(req, obj)
    subject = f"Observation for {label} {value}".replace("  ", " ") if label else f"Observation for {value}"
    signer, designation = getattr(user, "full_name", "") or user.email, getattr(user, "designation_name", "")
    lines = ["Dear Concern,", f"You have an observation on {value}", f"Observation is {comment}",
             "Please solve the issue ASAP and inform me.", "Thanks and Regards", signer, designation]
    body = "\n\n".join(lines[:5]) + "\n" + "\n".join(x for x in lines[5:] if x)
    url = _approval_url(req) if obj is None else (target_for(obj).url(obj) or "")
    try:
        send(user, to=[creator], subject=subject, body=body, url=url, company=req.company_id)
    except MailError:                                   # approver cannot reach the creator by mail: send it as the system
        send_system([creator], subject, body, url, company=req.company_id)
    html = "".join(f"<p>{x}</p>" for x in (
        "Dear Concern,", f"You have an observation on {escape(value)}", f"Observation is <strong>{escape(comment)}</strong>",
        "Please solve the issue ASAP and inform me.", "Thanks and Regards", escape(signer), escape(designation)))
    notify([creator], subject, body=comment, url=url, level="warning", kind="approval", company=req.company_id, email=False)
    if creator.email and (conf.get("CORE_NOTIFY_EMAIL") or req.approval_type.notify_email):
        transaction.on_commit(lambda: _html_email(creator.email, subject, body, html))


def _html_email(to: str, subject: str, text: str, html: str) -> None:
    try:
        from django.core.mail import EmailMultiAlternatives

        mail = EmailMultiAlternatives(subject, text, to=[to])
        mail.attach_alternative(html, "text/html")
        mail.send(fail_silently=True)
    except Exception:                                   # pragma: no cover - e-mail is best effort
        log.exception("observation e-mail failed")


def _reject(req: ApprovalRequest, obj, cfg, user, step: dict, comment: str) -> None:
    """Reject the step: back to the previous approved step (``previous_step`` types) or to the submitter. A rejection at an extra
    (added) step always returns the record to its creator, who is also mailed."""
    _history(req, Approval.REJECT, user, step, comment)
    previous = None
    if step.get("kind") == "extra":
        _mail(user, [_creator(req, obj)], f"Rejected: {req.object_repr}",
              f"{_name(user)} rejected the added step \"{step['name']}\" of {req.object_repr}.\n\n{comment}",
              cfg.url(obj) or _approval_url(req), company=req.company_id)
    elif req.reject_mode == ApprovalType.PREVIOUS_STEP:
        approved = set(req.history.filter(round=req.round, action__in=PASS_ACTIONS, sequence__lt=step["sequence"])
                       .values_list("sequence", flat=True))
        previous = next((s for s in reversed(req.chain) if s["sequence"] in approved), None)
    if previous is not None:
        req.current_sequence = previous["sequence"]
        req.save()
        _notify_step(req, previous, obj, cfg, " (sent back)")
        _notify_submitter(req, f"Sent back: {req.object_repr}", comment, obj, cfg, "warning")
        return
    req.status, req.completed_at, req.current_sequence = ApprovalRequest.REJECTED, timezone.now(), 0
    req.save()
    _set_flags(obj, confirm=False, approved=False)               # resubmittable; also resets a post-final rejection
    if cfg.on_rejected:
        cfg.on_rejected(obj, req)
    _notify_finished(req, ApprovalType.NOTIFY_REJECTED, f"Rejected: {req.object_repr}", comment, obj, cfg, "danger")
    _announce(obj, req, "rejected", user)


def cancel(user, obj, *, comment: str = "") -> None:
    """Cancel a ``CancelableModel`` row (also after approval): sets is_cancel/canceled_*, resets approval flags, cancels the request."""
    if not isinstance(obj, CancelableModel):
        return _withdraw(user, obj, comment)
    if not can_cancel(user, obj):
        raise ApprovalError("You are not allowed to cancel this record.")
    with transaction.atomic():
        locked = type(obj)._base_manager.select_for_update().get(pk=obj.pk)
        if locked.is_cancel:
            raise ApprovalError("This record is already cancelled.")
        now = timezone.now()
        values = {"is_cancel": True, "canceled_by": user, "canceled_at": now}
        if hasattr(locked, "is_approval"):
            values.update(is_approval=False, approved_at=None, is_confirm=False)
        from core.audit.service import audited_update

        audited_update(type(locked)._base_manager.filter(pk=locked.pk), message="cancelled", **values)
        for name, value in values.items():
            setattr(obj, name, value)
        req = (ApprovalRequest.objects.select_for_update().filter(content_type=_ct(locked), object_id=str(locked.pk),
                                                                  status__in=[ApprovalRequest.PENDING, ApprovalRequest.APPROVED])
               .order_by("-created_at", "-pk").first())
        if req is not None:
            req.status, req.completed_at, req.current_sequence = ApprovalRequest.CANCELLED, now, 0
            req.save()
            _history(req, Approval.CANCEL, user, None, comment)
            try:
                _notify_submitter(req, f"Cancelled: {req.object_repr}", comment, locked, target_for(locked), "warning")
            except ApprovalError:
                pass
        record_event("approval", obj=locked, message="cancelled", user=user)


def _withdraw(user, obj, comment: str) -> None:
    """Withdraw the pending request of a record that has no cancel fields: request -> cancelled, flags reset, record unlocked."""
    if not can_cancel(user, obj):
        raise ApprovalError("Only the person who submitted a request that is still waiting for approval can withdraw it.")
    with transaction.atomic():
        locked = type(obj)._base_manager.select_for_update().get(pk=obj.pk)
        req = (ApprovalRequest.objects.select_for_update().filter(content_type=_ct(locked), object_id=str(locked.pk),
                                                                  status=ApprovalRequest.PENDING).first())
        if req is None:
            raise ApprovalError("This record is not waiting for approval.")
        req.status, req.completed_at, req.current_sequence = ApprovalRequest.CANCELLED, timezone.now(), 0
        req.save()
        _history(req, Approval.CANCEL, user, None, comment or "Withdrawn")
        _set_flags(locked, confirm=False, approved=False)
        _copy_flags(obj, locked)
        if req.submitted_by_id != user.pk:
            _notify_submitter(req, f"Withdrawn: {req.object_repr}", comment, locked, target_for(locked), "warning")


def statuses_for(objs) -> dict:
    """{pk: status} for a page of records in one query (``none`` when never submitted) -- list pages."""
    objs = list(objs)
    if not objs:
        return {}
    ct = _ct(objs[0])
    latest: dict = {}
    for object_id, status in (ApprovalRequest.objects.filter(content_type=ct, object_id__in=[str(o.pk) for o in objs])
                              .order_by("object_id", "-created_at", "-pk").values_list("object_id", "status")):
        latest.setdefault(object_id, status)
    return {o.pk: latest.get(str(o.pk), "none") for o in objs}


def can_add_step(user, req: ApprovalRequest) -> bool:
    """May ``user`` add a step to this pending request? Superusers, holders of ``core.add_step_to_request`` (every approval type)
    and the people listed under *may add steps* on the request's approval type -- provided they can see the request."""
    if not getattr(user, "is_authenticated", False) or not user.is_active or req.status != ApprovalRequest.PENDING:
        return False
    return _adds_steps(user, req) and can_view(user, req)


def step_approver_choices(req: ApprovalRequest):
    """Who may be named approver of an added step: active members of the request's company (never the submitter, unless
    self-approval is on, since they could not decide it)."""
    from django.contrib.auth import get_user_model

    people = get_user_model().objects.filter(is_active=True)
    if req.company_id:
        people = people.filter(profile__companies=req.company_id)
    if req.submitted_by_id and not conf.get("CORE_SELF_APPROVAL"):
        people = people.exclude(pk=req.submitted_by_id)
    return people.distinct().order_by("full_name", "email")


def _name(user) -> str:
    return (getattr(user, "full_name", "") or getattr(user, "email", "")) if user is not None else "System"


def add_step_to_request(user, request_id: int, step: dict, *, position: str = "after", after_sequence: int | None = None,
                        expected_sequence: int | None = None, comment: str = "") -> ApprovalRequest:
    """Add an extra step (name + approver users) to one pending request (see :func:`can_add_step`).

    ``position="before"``: the new step goes right before the step waiting now and opens at once; the waiting step follows once
    it is passed. ``position="after"``: it goes right after the waiting step (or after the later step ``after_sequence``), never
    after the final step. The new step's approvers and the record's creator are mailed; a rejection at the added step returns
    the record to its creator (whatever the type's reject mode)."""
    if position not in ("before", "after"):
        raise ApprovalError("Choose where the new step goes.")
    extra = _extra_defaults(step)
    with transaction.atomic():
        try:
            req = ApprovalRequest.objects.select_for_update().select_related("approval_type", "content_type").get(pk=request_id)
        except ApprovalRequest.DoesNotExist:
            raise ApprovalError("That approval request does not exist.") from None
        if not _member(user, req.company_id):
            raise ApprovalError("That approval request does not exist.")
        if req.status != ApprovalRequest.PENDING:
            raise ApprovalError("Steps can only be added to a request that is still pending.")
        if not can_add_step(user, req):
            raise ApprovalError("You are not allowed to add approval steps to this request.")
        if expected_sequence is not None and int(expected_sequence) != req.current_sequence:
            raise ApprovalError("This request changed while you were looking at it. Reload and try again.")
        if extra["approver_user_ids"]:
            allowed = set(step_approver_choices(req).filter(pk__in=extra["approver_user_ids"]).values_list("pk", flat=True))
            if allowed != set(extra["approver_user_ids"]):
                raise ApprovalError("Pick approvers who work for the record's company (not the person who sent it).")
        old_current = req.current_sequence
        waiting = req.step_at(old_current) or {}
        chain = req.chain
        if position == "before":
            new = _insert_extra(chain, extra, before_sequence=old_current)
        else:
            if after_sequence is not None and after_sequence < old_current:
                raise ApprovalError("A step cannot be inserted before the step that is waiting now.")
            new = _insert_extra(chain, extra, after_sequence if after_sequence is not None else old_current)
        if any("_old" in s for s in chain):                       # chain was respaced: keep the current step pointer
            req.current_sequence = next(s["sequence"] for s in chain if s.get("_old") == old_current)
            for s in chain:
                s.pop("_old", None)
        _validate_chain(chain)
        if position == "before":
            req.current_sequence = new["sequence"]
        req.chain, req.extra_steps = chain, [*req.extra_steps, new]
        req.save()
        where = f"{position} \"{waiting.get('name', '')}\""
        _history(req, Approval.ADD_STEP, user, new, comment or f"Step added {where}")
        obj = _target(req)
        if obj is not None:
            cfg = target_for(obj)
            if position == "before":
                _notify_step(req, new, obj, cfg)                  # it is the step waiting now
            _mail_step_added(req, obj, cfg, new, user, where, now=position == "before", comment=comment)
        return req


def _mail_step_added(req: ApprovalRequest, obj, cfg, step: dict, actor, where: str, *, now: bool, comment: str) -> None:
    from django.contrib.auth import get_user_model

    approvers = list(get_user_model().objects.filter(pk__in=_user_ids(step), is_active=True).order_by("full_name", "email"))
    subject = f"Approval step added: {req.object_repr}"
    note = f"\n\n{comment}" if comment else ""
    when = "It is waiting for your decision now." if now else "It comes to you once the step before it is passed."
    _mail(actor, approvers, subject, f"{_name(actor)} added the step \"{step['name']}\" ({where}) to the approval of "
                                     f"{req.object_repr} and named you as its approver. {when}{note}", _approval_url(req),
          company=req.company_id)
    names = ", ".join(_name(u) for u in approvers) or "people with the step's permission"
    _mail(actor, [_creator(req, obj)], subject, f"{_name(actor)} added the step \"{step['name']}\" ({where}) to the approval of "
                                              f"{req.object_repr}. Approver: {names}.{note}", cfg.url(obj) or _approval_url(req),
          company=req.company_id)


# --------------------------------------------------------------------------------------------------------------- queues
def get_pending_approvals(user, limit: int = 200) -> list[ApprovalRequest]:
    """Pending requests ``user`` can decide right now (company scoped, step scoped, self-approval aware)."""
    members = user_company_ids(user)
    if not members:
        return []
    qs = (ApprovalRequest.objects.filter(status=ApprovalRequest.PENDING).filter(Q(company_id__in=members) | Q(company__isnull=True))
          .select_related("approval_type", "content_type", "submitted_by").order_by("created_at", "pk")[:500])
    return [r for r in qs if can_act(user, r)][:limit]


def pending_count(user) -> int:
    return len(get_pending_approvals(user))


def my_requests(user):
    members = user_company_ids(user)
    from django.db.models import Q

    return ApprovalRequest.objects.filter(submitted_by=user).filter(Q(company_id__in=members) | Q(company__isnull=True))
