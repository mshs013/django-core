"""Request pipeline pieces: acting context, active company, idle timeout, URL-name permission enforcement, security headers."""
from __future__ import annotations

import logging
import time
import uuid

from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import Resolver404, resolve, reverse
from django.utils.deprecation import MiddlewareMixin

from core import conf
from core.permissions import active_company_id, user_company_ids, user_has_perm
from core.services import context

log = logging.getLogger("core.request")


def client_ip(request) -> str:
    # REMOTE_ADDR only; deployments behind a trusted proxy set it via the proxy-aware server (see docs/DEPLOYMENT.md).
    return request.META.get("REMOTE_ADDR", "")


class RequestContextMiddleware(MiddlewareMixin):
    """Puts user/ip/company into contextvars (created_by stamps, audit rows) and a request id on the response."""

    def process_request(self, request):
        user = getattr(request, "user", None)
        company_id = active_company_id(user) if user is not None and user.is_authenticated else None
        request.core_company_id = company_id
        request.core_member_ids = user_company_ids(user) if user is not None else frozenset()
        request.core_request_id = uuid.uuid4().hex[:12]
        request._core_ctx_tokens = context.set_context(user, client_ip(request), frozenset({company_id}) if company_id else frozenset(),
                                                       None)

    def process_response(self, request, response):
        tokens = getattr(request, "_core_ctx_tokens", None)
        if tokens:
            try:
                context.reset_context(tokens)
            except ValueError:
                pass
        response["X-Request-ID"] = getattr(request, "core_request_id", "")
        return response

    def process_exception(self, request, exception):
        tokens = getattr(request, "_core_ctx_tokens", None)
        if tokens:
            try:
                context.reset_context(tokens)
            except ValueError:
                pass
            request._core_ctx_tokens = None
        return None


class IdleTimeoutMiddleware(MiddlewareMixin):
    """Signs the user out after ``CORE_IDLE_TIMEOUT_SECONDS`` without a request (server-side, independent of cookie age)."""

    def process_request(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        now = int(time.time())
        last = request.session.get("core_last_activity")
        limit = conf.get("CORE_IDLE_TIMEOUT_SECONDS")
        if last is not None and limit and now - int(last) > limit:
            from core.views.auth import audit_auth

            audit_auth("logout", request, user, "idle timeout")
            logout(request)
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=401)
                response["HX-Redirect"] = reverse("core:login")
                return response
            return HttpResponseRedirect(f"{reverse('core:login')}?timeout=1")
        request.session["core_last_activity"] = now
        return None


class UrlPermissionMiddleware(MiddlewareMixin):
    """Default-deny authorisation by URL name. ``LoginRequiredMiddleware`` has already forced authentication.

    * views marked ``@login_not_required`` AND whose name is in ``CORE_PUBLIC_URL_NAMES`` stay public;
    * every other named URL needs ``core.access_<namespace>_<name>`` (created by ``seed_url_permissions``) unless the view declares
      its own permission checks via ``core_self_authorizing = True``;
    * UNNAMED URLs are refused for everybody except superusers (an unnamed endpoint can never bypass authorisation)."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not conf.get("CORE_ENFORCE_URL_PERMISSIONS"):
            return None
        match = getattr(request, "resolver_match", None)
        name = match.view_name if match else ""
        if getattr(view_func, "login_required", True) is False:
            if name in conf.get("CORE_PUBLIC_URL_NAMES"):
                return None
            raise PermissionDenied                                    # public marker on a non-allowlisted endpoint: refuse
        user = request.user
        if not user.is_authenticated:
            raise PermissionDenied
        if user.is_superuser:
            return None
        if not name:
            raise PermissionDenied
        target = getattr(view_func, "view_class", view_func)
        if getattr(target, "core_self_authorizing", False):
            return None
        from core.permissions.seed import permission_codename

        if not user_has_perm(user, "core." + permission_codename(name)):
            raise PermissionDenied
        return None


class SecurityHeadersMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self' 'unsafe-eval' 'unsafe-inline'; font-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        if request.user.is_authenticated if hasattr(request, "user") else False:
            response.setdefault("Cache-Control", "private, no-store")
        return response
