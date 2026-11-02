"""Cache invalidation hooks: any change to Group/Permission/User/Company/membership drops cached permission answers and menus."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models.signals import m2m_changed, post_delete, post_save

from core.permissions import cache


def _invalidate(sender, **kwargs):
    if kwargs.get("raw"):
        return
    cache.invalidate_all()


def connect():
    from core.models import Company, MenuItem, ObjectAccess, Profile, Role, UserAccess

    User = get_user_model()
    for model in (Group, Role, Permission, User, Company, Profile, MenuItem, ObjectAccess, UserAccess):
        post_save.connect(_invalidate, sender=model, dispatch_uid=f"core_perm_save_{model._meta.label}")
        post_delete.connect(_invalidate, sender=model, dispatch_uid=f"core_perm_delete_{model._meta.label}")
    for through in (User.groups.through, User.user_permissions.through, Group.permissions.through, Profile.companies.through):
        m2m_changed.connect(_invalidate, sender=through, dispatch_uid=f"core_perm_m2m_{through._meta.label}")
    try:
        from guardian.models import GroupObjectPermission, UserObjectPermission

        for model in (UserObjectPermission, GroupObjectPermission):
            post_save.connect(_invalidate, sender=model, dispatch_uid=f"core_perm_save_{model._meta.label}")
            post_delete.connect(_invalidate, sender=model, dispatch_uid=f"core_perm_delete_{model._meta.label}")
    except ImportError:  # pragma: no cover
        pass
