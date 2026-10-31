from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from core.models import Notification
from core.notifications.service import mark_read, unread_count
from core.permissions import PAGE_PERMS
from core.search.builder.state import apply_state, paginate
from core.search.errors import SearchError
from core.search.registry import get_search

from .base import CoreMixin, safe_next
from .crud import build_search_state, builder_fields


class NotificationList(CoreMixin, View):
    """Own notifications only; same quick-search + builder infrastructure as every list."""

    required_perm = PAGE_PERMS["notifications"]

    def get(self, request):
        spec, state, saved = build_search_state(request, "notifications", "resource", self.ctx)
        qs = Notification.objects.filter(recipient=request.user).select_related("company")
        try:
            qs = apply_state(spec, qs, state)
        except SearchError as exc:
            state.warnings.append(str(exc))
            qs = Notification.objects.filter(recipient=request.user).select_related("company").order_by("-created_at", "-pk")
        from core.search.registry import OPERATOR_LABELS

        return render(request, "core/notifications/list.html", {
            "page": paginate(qs, state, 25), "state": state, "spec": spec, "saved_list": saved, "builder_fields": builder_fields(spec),
            "operator_labels": OPERATOR_LABELS, "target_type": "resource", "target_key": "notifications", "unread": unread_count(request.user)})

    def post(self, request):
        ids = request.POST.getlist("ids") or None
        if request.POST.get("action") == "mark_all":
            ids = None
        mark_read(request.user, ids)
        return redirect(safe_next(request, "core:notification_list"))


class NotificationDropdown(CoreMixin, View):
    required_perm = PAGE_PERMS["notifications"]

    def get(self, request):
        items = Notification.objects.filter(recipient=request.user).select_related("company").order_by("-created_at", "-pk")[:8]
        return render(request, "core/notifications/_dropdown.html", {"items": items, "unread": unread_count(request.user)})


class NotificationBadge(CoreMixin, View):
    """Top-bar unread badge, polled by the bell so new notices show without a page reload."""

    required_perm = PAGE_PERMS["notifications"]

    def get(self, request):
        return render(request, "core/notifications/_badge.html", {"core_unread": unread_count(request.user)})


class NotificationOpen(CoreMixin, View):
    """Mark one read and go to its (internal) link."""

    required_perm = PAGE_PERMS["notifications"]

    def get(self, request, pk):
        note = Notification.objects.filter(pk=pk, recipient=request.user).first()
        if note is None:
            return redirect("core:notification_list")
        note.mark_read()
        return redirect(note.url if note.url.startswith("/") and not note.url.startswith("//") else "core:notification_list")
