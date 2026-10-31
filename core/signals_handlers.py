"""Receivers: profile creation, permission/menu cache invalidation (Group/Permission/User/Company/membership changes, saved reports
placed in the menu), default data after ``migrate`` (:func:`seed_defaults_after_migrate`)."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from core.permissions import cache


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="core_profile_for_user")
def create_profile(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from core.services.org import ensure_profile

        ensure_profile(instance)


def _bump(sender, **kwargs):
    if not kwargs.get("raw"):
        cache.bump()


def connect_cache_invalidation():
    from core.models import Company, MenuItem, Profile
    from django.contrib.auth import get_user_model

    User = get_user_model()
    for model in (Group, Permission, User, Company, Profile, MenuItem):
        post_save.connect(_bump, sender=model, dispatch_uid=f"core_bump_save_{model.__name__}", weak=False)
        post_delete.connect(_bump, sender=model, dispatch_uid=f"core_bump_del_{model.__name__}", weak=False)
    for through in (User.groups.through, User.user_permissions.through, Group.permissions.through, Profile.companies.through):
        m2m_changed.connect(_bump, sender=through, dispatch_uid=f"core_bump_m2m_{through.__name__}", weak=False)


def _report_menu_before(sender, instance, raw=False, **kwargs):
    """Remember whether a saved report was in the menu before this save (moving it out must drop the cached menus too)."""
    instance._core_menu_before = (None if raw or instance.pk is None else
                                  sender._base_manager.filter(pk=instance.pk).values_list("menu_parent_id", flat=True).first())


def _report_menu_saved(sender, instance, raw=False, **kwargs):
    if not raw and (instance.menu_parent_id or getattr(instance, "_core_menu_before", None)):
        cache.bump()


def _report_menu_deleted(sender, instance, **kwargs):
    if instance.menu_parent_id:
        cache.bump()


def _report_menu_shared(sender, instance, action, reverse=False, **kwargs):
    """Sharing decides who sees a report in the menu; ``reverse``: changed from the user / group side."""
    if action.startswith("post_") and (reverse or getattr(instance, "menu_parent_id", None)):
        cache.bump()


def connect_report_menu_invalidation():
    """Saved reports placed in the menu: a change of placement, name, visibility, sharing or a delete clears the cached menus."""
    from django.db.models.signals import pre_save

    from core.models import QueryReport, ReportDefinition

    for model in (ReportDefinition, QueryReport):
        pre_save.connect(_report_menu_before, sender=model, dispatch_uid=f"core_menu_report_pre_{model.__name__}", weak=False)
        post_save.connect(_report_menu_saved, sender=model, dispatch_uid=f"core_menu_report_save_{model.__name__}", weak=False)
        post_delete.connect(_report_menu_deleted, sender=model, dispatch_uid=f"core_menu_report_del_{model.__name__}", weak=False)
        for through in (model.shared_users.through, model.shared_groups.through):
            m2m_changed.connect(_report_menu_shared, sender=through, dispatch_uid=f"core_menu_report_m2m_{through.__name__}", weak=False)


connect_cache_invalidation()
connect_report_menu_invalidation()


from django.core.signals import setting_changed  # noqa: E402


@receiver(setting_changed, dispatch_uid="core_display_formats_changed")
def reset_display_formats(sender, setting, **kwargs):
    """Django caches formats per language; a changed ``CORE_*_FORMAT`` (tests, ``override_settings``) must not be served stale."""
    if setting in ("CORE_DATE_FORMAT", "CORE_DATETIME_FORMAT", "CORE_TIME_FORMAT"):
        from django.utils.formats import reset_format_cache

        reset_format_cache()


def seed_defaults_after_migrate(sender, using="default", apps=None, **kwargs):
    """``post_migrate`` (core only): give a fresh database the default menu (``core/menu/defaults.py``: final permissions and
    superuser flags). It fills an EMPTY menu table only, so it never re-adds, edits or deletes the administrator's rows;
    ``manage.py seed_default_menu`` adds missing default rows on demand. Permission rows and content types come from Django's own
    ``post_migrate`` receivers (auth / contenttypes). Runs inside a savepoint: a failure here must not abort the migrate's
    transaction on PostgreSQL."""
    from django.db import connections, transaction

    from core.menu.defaults import seed

    try:
        MenuItem = apps.get_model("core", "MenuItem") if apps is not None else None
    except LookupError:                                   # ``migrate core zero``: no menu model in this state
        return
    if MenuItem is None or MenuItem._meta.db_table not in connections[using].introspection.table_names():
        return
    with transaction.atomic(using=using):
        if MenuItem.objects.using(using).exists():
            return
        created = seed(MenuItem, using=using)
    if created:
        cache.bump()
