"""View helpers: permission + company checks that 404 (not 403) for objects outside the user's companies."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404

from core.permissions.service import filter_by_perm, user_has_perm


class PermissionRequiredMixin:
    """``required_permission`` = 'app_label.codename'; a global OR object-level grant is checked per object in ``get_object``."""

    required_permission: str | None = None

    def has_model_permission(self, request) -> bool:
        return not self.required_permission or user_has_perm(request.user, self.required_permission, company=None) or \
            self._has_any_object_grant(request)

    def _has_any_object_grant(self, request) -> bool:
        return False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not self.has_model_permission(request):
            raise DjangoPermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def handle_no_permission(self):
        from django.contrib.auth.views import redirect_to_login

        raise DjangoPermissionDenied


def get_scoped_object_or_404(queryset, user, perm: str, **lookup):
    """Fail-closed object fetch: outside the user's companies or without the permission -> 404 (no ID guessing oracle)."""
    qs = filter_by_perm(user, perm, queryset)
    try:
        return qs.get(**lookup)
    except (queryset.model.DoesNotExist, ValueError, TypeError, queryset.model.MultipleObjectsReturned):
        raise Http404("Not found")
