"""Authentication views: login (throttled, audited), logout (POST), password change/reset, profile, company switcher. No 2FA."""
from __future__ import annotations

import time

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import (PasswordResetCompleteView, PasswordResetConfirmView, PasswordResetDoneView,
                                       PasswordResetView)
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from core.audit.service import record_event
from core.crud.forms import style_form
from core.middleware import client_ip
from core.models import Company
from core.permissions import active_company_id, user_company_ids
from core.services import throttle

from .base import CoreMixin, safe_next


def audit_auth(event: str, request, user=None, message: str = "", email: str = "") -> None:
    """Sign-in / sign-out / password rows: the user (or the e-mail typed, for a failed sign-in), the client IP and the user's active
    company. Never the password."""
    known = user if user is not None and getattr(user, "pk", None) else None
    record_event(event, user=known, user_repr=email[:200], message=message, ip=client_ip(request) or None,
                 company=active_company_id(known) if known is not None else None, model_label="core.User" if known is None else "",
                 object_pk=str(known.pk) if known is not None else "", object_repr=str(known) if known is not None else email[:200])


@method_decorator([login_not_required, never_cache, csrf_protect], name="dispatch")
class LoginView(View):
    template_name = "core/auth/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return render(request, self.template_name, {"timeout": request.GET.get("timeout")})

    def post(self, request):
        email = (request.POST.get("email") or "").strip().lower()[:254]
        password = request.POST.get("password") or ""
        ip = client_ip(request)
        if throttle.is_locked(email, ip):
            audit_auth("login_failed", request, message="locked out by throttling", email=email)
            return render(request, self.template_name, {"error": "Too many attempts. Try again later.", "email": email}, status=429)
        user = authenticate(request, email=email, password=password)
        if user is None or not user.is_active:
            throttle.register_failure(email, ip)
            audit_auth("login_failed", request, message="invalid credentials", email=email)
            return render(request, self.template_name, {"error": "Invalid email or password.", "email": email}, status=400)
        throttle.reset(email, ip)
        login(request, user)                                     # rotates the session key
        request.session["core_last_activity"] = int(time.time())
        audit_auth("login", request, user, "signed in")
        return redirect(safe_next(request, reverse("core:dashboard")))


class LogoutView(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        audit_auth("logout", request, request.user, "signed out")
        logout(request)
        return redirect("core:login")


class PasswordChange(CoreMixin, View):
    def get(self, request):
        form = PasswordChangeForm(request.user)
        style_form(form)
        return render(request, "core/auth/password_change.html", {"form": form})

    def post(self, request):
        form = PasswordChangeForm(request.user, request.POST)
        style_form(form)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            audit_auth("other", request, user, "password changed")
            messages.success(request, "Password changed.")
            return redirect("core:profile")
        return render(request, "core/auth/password_change.html", {"form": form}, status=400)


@method_decorator(login_not_required, name="dispatch")
class PasswordReset(PasswordResetView):
    template_name = "core/auth/password_reset.html"
    email_template_name = "core/auth/password_reset_email.txt"
    subject_template_name = "core/auth/password_reset_subject.txt"
    success_url = reverse_lazy("core:password_reset_done")

    def form_valid(self, form):
        email = (form.cleaned_data.get("email") or "").strip().lower()
        from django.contrib.auth import get_user_model

        account = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        audit_auth("other", request=self.request, user=account, message="password reset requested", email=email)
        return super().form_valid(form)


@method_decorator(login_not_required, name="dispatch")
class PasswordResetDone(PasswordResetDoneView):
    template_name = "core/auth/password_reset_done.html"


@method_decorator(login_not_required, name="dispatch")
class PasswordResetConfirm(PasswordResetConfirmView):
    template_name = "core/auth/password_reset_confirm.html"
    success_url = reverse_lazy("core:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit_auth("other", self.request, form.user, "password reset completed")
        return response


@method_decorator(login_not_required, name="dispatch")
class PasswordResetComplete(PasswordResetCompleteView):
    template_name = "core/auth/password_reset_complete.html"


class ProfileView(CoreMixin, View):
    def get(self, request):
        profile = request.user.profile
        return render(request, "core/auth/profile.html", {"profile": profile, "companies": profile.companies.filter(deleted_at__isnull=True)})

    def post(self, request):
        profile = request.user.profile
        request.user.full_name = (request.POST.get("full_name") or "")[:150]
        request.user.save(update_fields=["full_name"])
        profile.contact_no = (request.POST.get("phone") or "")[:50]
        if request.POST.get("theme") in ("system", "light", "dark"):
            profile.theme = request.POST["theme"]
        profile.save(update_fields=["contact_no", "theme"])
        messages.success(request, "Profile saved.")
        return redirect("core:profile")


class ThemeSet(CoreMixin, View):
    """POST theme=system|light|dark -> saved on the profile (the browser also remembers it in localStorage)."""

    http_method_names = ["post"]

    def post(self, request):
        choice = request.POST.get("theme")
        if choice not in ("system", "light", "dark"):
            return HttpResponse(status=400)
        profile = request.user.profile
        if profile.theme != choice:
            profile.theme = choice
            profile.save(update_fields=["theme"])
        return HttpResponse(status=204)


class SwitchCompany(CoreMixin, View):
    """POST company id -> active company. Only companies the user is a member of are accepted (anything else is a no-op error)."""

    http_method_names = ["post"]

    def post(self, request):
        try:
            wanted = int(request.POST.get("company", ""))
        except ValueError:
            wanted = None
        if wanted is None or wanted not in user_company_ids(request.user):
            messages.error(request, "You are not a member of that company.")
            return redirect(safe_next(request, reverse("core:dashboard")))
        profile = request.user.profile
        profile.active_company_id = wanted
        profile.save(update_fields=["active_company"])
        from core.permissions import cache

        cache.bump()
        record_event("other", user=request.user, message="switched company", company=wanted)
        messages.success(request, f"Active company: {Company.objects.get(pk=wanted).name}")
        home = reverse("core:dashboard")
        target = safe_next(request, home)
        return redirect(target if target == home or can_open(request, target) else home)


class _DiscardMessages:
    """Message storage for :func:`can_open`: the trial request must not queue toasts for the real one."""

    def add(self, *args, **kwargs):
        pass

    def __iter__(self):
        return iter(())


def can_open(request, url: str) -> bool:
    """True when ``request.user`` may open ``url`` now: the page is dispatched as a GET (URL permission + the view's own checks,
    company scope included) and must answer without an error. Used to return to the current page after a company switch."""
    import copy

    from django.core.exceptions import PermissionDenied
    from django.http import Http404, QueryDict
    from django.utils.datastructures import MultiValueDict
    from django.urls import Resolver404, resolve

    from core.middleware import UrlPermissionMiddleware

    path, _, query = url.partition("?")
    try:
        match = resolve(path)
    except Resolver404:
        return False
    trial = copy.copy(request)
    trial.method, trial.path, trial.path_info, trial.resolver_match = "GET", path, path, match
    trial.GET, trial._post, trial._files = QueryDict(query), QueryDict(), MultiValueDict()
    trial.META = {k: v for k, v in request.META.items() if not k.startswith("HTTP_HX_")}
    trial.META.update(REQUEST_METHOD="GET", QUERY_STRING=query, PATH_INFO=path)
    trial._messages = _DiscardMessages()
    try:
        if UrlPermissionMiddleware(lambda r: None).process_view(trial, match.func, match.args, match.kwargs) is not None:
            return False
        response = match.func(trial, *match.args, **match.kwargs)
    except (PermissionDenied, Http404):
        return False
    except Exception:                                   # a page that breaks under the new company is not a place to land on
        return False
    return response.status_code < 400
