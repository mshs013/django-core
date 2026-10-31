"""Approval screens: my queue, my requests, request detail (timeline + actions incl. Add step), submit/resubmit/cancel actions."""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core import approvals
from core.approvals import ApprovalError
from core.approvals.service import step_ui
from core.crud.access import get_object_or_404 as scoped_get
from core.crud.registry import get_resource
from core.models import ApprovalRequest
from core.permissions import PAGE_PERMS

from .base import CoreMixin, safe_next


def _queue_tabs(user) -> list[dict]:
    """Pending requests grouped by approval type (one tab each). Each tab shows the record columns chosen on the type
    (``queue_fields``), who created the record and when, and the current step's decision labels."""
    from core.approvals.registry import target_for
    from core.crud.display import display_value, label_for

    requests = approvals.get_pending_approvals(user)
    wanted: dict = {}
    for r in requests:
        wanted.setdefault(r.content_type_id, set()).add(r.object_id)
    objects = {}
    for ct_id, ids in wanted.items():                     # one query per model, not one per row
        model = next(r.content_type for r in requests if r.content_type_id == ct_id).model_class()
        if model is not None:
            for obj in model._base_manager.filter(pk__in=list(ids)):
                objects[(ct_id, str(obj.pk))] = obj
    tabs: dict = {}
    for r in requests:
        obj = objects.get((r.content_type_id, str(r.object_id)))
        model = r.content_type.model_class()
        tab = tabs.get(r.approval_type_id)
        if tab is None:
            fields = [f for f in r.approval_type.queue_field_names() if model is not None and _has_field(model, f)]
            tab = tabs[r.approval_type_id] = {"type": r.approval_type, "fields": fields, "rows": [],
                                              "headers": [label_for(model, f) for f in fields]}
        try:
            url = target_for(obj).url(obj) if obj is not None else ""
        except ApprovalError:
            url = ""
        tab["rows"].append({
            "req": r, "obj": obj, "url": url, "ui": step_ui(r.step_at(r.current_sequence)),
            "cells": [display_value(obj, f)[1] if obj is not None else "" for f in tab["fields"]],
            "can_add_step": approvals.can_add_step(user, r),
            "created_by": getattr(obj, "created_by", None) or r.submitted_by,
            "created_at": getattr(obj, "created_at", None) or r.created_at})
    return list(tabs.values())


def _has_field(model, name: str) -> bool:
    from django.core.exceptions import FieldDoesNotExist

    try:
        model._meta.get_field(name)
        return True
    except FieldDoesNotExist:
        return False


class Queue(CoreMixin, View):
    required_perm = PAGE_PERMS["approvals"]

    def get(self, request):
        tabs = _queue_tabs(request.user)
        active = request.GET.get("type", "")
        if not any(str(t["type"].pk) == active for t in tabs):
            active = str(tabs[0]["type"].pk) if tabs else ""
        return render(request, "core/approvals/queue.html", {"tabs": tabs, "active": active})


class MyRequests(CoreMixin, View):
    required_perm = PAGE_PERMS["approvals"]

    def get(self, request):
        return render(request, "core/approvals/my.html", {"requests": approvals.my_requests(request.user).select_related("approval_type")[:100]})


class RequestDetail(CoreMixin, View):
    def _request(self, request, pk):
        req = get_object_or_404(ApprovalRequest.objects.select_related("approval_type", "content_type", "submitted_by"), pk=pk)
        if not approvals.can_view(request.user, req):
            raise Http404                                    # no probing of other companies' / users' requests
        return req

    def get(self, request, pk):
        req = self._request(request, pk)
        target = req.content_type.model_class()._base_manager.filter(pk=req.object_id).first() if req.content_type.model_class() else None
        link = ""
        try:
            from core.approvals.registry import get_target

            link = get_target(req.target_key).url(target) if target is not None else ""
        except ApprovalError:
            pass
        can_add = approvals.can_add_step(request.user, req)
        current = req.step_at(req.current_sequence) or {}
        return render(request, "core/approvals/detail.html", {
            "req": req, "history": req.history.select_related("actor"), "can_act": approvals.can_act(request.user, req), "target_url": link,
            "can_add_step": can_add, "chain": req.chain, "current": current, "ui": step_ui(current or None),
            "step_people": approvals.step_approver_choices(req) if can_add else ()})

    def post(self, request, pk):
        req = self._request(request, pk)
        action = request.POST.get("action")
        try:
            with transaction.atomic():
                if action in ("approve", "reject", "observe"):
                    approvals.submit_approval_step(request.user, req.pk, action, comment=request.POST.get("comment", ""),
                                                   expected_sequence=request.POST.get("expected_sequence") or None)
                    messages.success(request, "Observation sent; the request moved on." if action == "observe" else "Decision recorded.")
                elif action == "add_step":
                    approvers = [int(i) for i in request.POST.getlist("approvers") if i.isdigit()]
                    if not approvers:
                        raise ApprovalError("Pick at least one approver for the new step.")
                    approvals.add_step_to_request(request.user, req.pk, {"name": request.POST.get("name", ""), "approver_user_ids": approvers},
                                                  position=request.POST.get("position", "after"), comment=request.POST.get("comment", ""),
                                                  expected_sequence=request.POST.get("expected_sequence") or None)
                    messages.success(request, "Step added; its approvers and the record's creator have been mailed.")
                else:
                    raise Http404
        except ApprovalError as exc:
            messages.error(request, str(exc))
        return redirect(safe_next(request, reverse("core:approval_detail", args=[req.pk])))      # the queue posts with ?next=


class SubmitForApproval(CoreMixin, View):
    """Detail-page button (no post_save signal): POST /approvals/submit/<resource slug>/<pk>/ -> submit | resubmit | cancel."""

    http_method_names = ["post"]

    def post(self, request, slug, pk, action):
        cfg = get_resource(slug)
        if cfg is None:
            raise Http404
        obj = scoped_get(cfg, self.ctx, pk, action="view")
        back = reverse("core:resource_detail", args=[slug, pk])
        try:
            with transaction.atomic():
                if action == "submit":
                    approvals.submit_for_approval(request.user, obj, comment=request.POST.get("comment", ""))
                    messages.success(request, "Sent for approval.")
                elif action == "resubmit":
                    approvals.resubmit(request.user, obj, comment=request.POST.get("comment", ""))
                    messages.success(request, "Resubmitted for approval.")
                elif action == "cancel":
                    approvals.cancel(request.user, obj, comment=request.POST.get("comment", ""))
                    messages.success(request, "Cancelled.")
                else:
                    raise Http404
        except ApprovalError as exc:
            messages.error(request, str(exc))
        return redirect(safe_next(request, back))
