"""What a user may hand out to others. A superuser may grant anything; anyone else (e.g. staff editing roles or users) may only
grant permissions they hold themselves, and only roles made entirely of such permissions -- so editing roles or users can
never raise anybody above the editor."""
from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.db.models import Q


def grantable_permissions(user):
    qs = Permission.objects.select_related("content_type")
    if user.is_superuser:
        return qs
    query = Q(pk__in=[])
    for full in user.get_all_permissions():
        app_label, _, codename = full.partition(".")
        query |= Q(content_type__app_label=app_label, codename=codename)
    return qs.filter(query)


def grantable_roles(user):
    if user.is_superuser:
        return Group.objects.all()
    allowed = grantable_permissions(user).values("pk")
    return Group.objects.exclude(permissions__in=Permission.objects.exclude(pk__in=allowed))


def can_grant_role(user, group) -> bool:
    return user.is_superuser or grantable_roles(user).filter(pk=group.pk).exists()
