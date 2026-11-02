from __future__ import annotations

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.cache import never_cache

from core import approvals
from core.notifications.service import unread_count

from .base import CoreMixin


class Dashboard(CoreMixin, View):
    def get(self, request):
        from core.models import ApprovalRequest

        return render(request, "core/dashboard.html", {
            "pending": approvals.pending_count(request.user), "unread": unread_count(request.user),
            "my_open": approvals.my_requests(request.user).filter(status=ApprovalRequest.PENDING).count()})


@login_not_required
@never_cache
def health(request):
    """Liveness: the process answers. No dependencies are touched and nothing sensitive is returned."""
    return JsonResponse({"status": "ok"})


@login_not_required
@never_cache
def ready(request):
    """Readiness: the database answers a trivial query. Details are logged, never returned."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("core.health").exception("readiness check failed")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


def error_403(request, exception=None):
    return render(request, "core/errors/403.html", {"detail": str(exception) if exception and str(exception) else ""}, status=403)


def error_404(request, exception=None):
    """Signed-out visitors are sent to the login page exactly as for a real page, so they cannot tell which URLs exist."""
    if not getattr(getattr(request, "user", None), "is_authenticated", False):
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    return render(request, "core/errors/404.html", status=404)


def error_500(request):
    return render(request, "core/errors/500.html", status=500)
