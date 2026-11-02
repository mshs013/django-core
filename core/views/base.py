from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.crud.access import Ctx


class CoreMixin:
    """Base for ``core`` views. They enforce their own (object-level) permissions, hence ``core_self_authorizing``: the URL-name
    permission middleware is for views that do NOT (e.g. business views)."""

    core_self_authorizing = True
    required_perm = ""          # page-level permission checked before the view runs ("" = only the view's own checks)

    def dispatch(self, request, *args, **kwargs):
        if self.required_perm:
            from core.permissions import user_has_perm

            if not user_has_perm(request.user, self.required_perm):
                raise PermissionDenied
        self.ctx = Ctx.from_request(request)
        return super().dispatch(request, *args, **kwargs)

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    def require_company(self):
        if self.ctx.company_id is None:
            raise PermissionDenied("Choose an active company first.")

    def toast(self, level: str, text: str) -> None:
        messages.add_message(self.request, {"success": messages.SUCCESS, "error": messages.ERROR, "warning": messages.WARNING}.get(level, messages.INFO), text)


def safe_next(request, default: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return default


def htmx_redirect(request, url: str):
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)
