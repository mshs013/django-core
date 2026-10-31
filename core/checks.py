"""System checks. ``core.E001``: a SoftDeleteModel's default manager must never expose deleted rows. ``core.E020``: the upload /
backup / archive folders must be writable by the user the app runs as. ``core.E030``: every database uses a supported engine
(PostgreSQL, MySQL/MariaDB, SQLite; read from settings, no connection is opened -- ``manage.py dbinfo --live`` checks versions)."""
import os

from django.apps import apps
from django.core.checks import Error, register


@register()
def soft_delete_managers(app_configs, **kwargs):
    from core.models.base import ALIVE, SoftDeleteModel

    errors = []
    for model in apps.get_models():
        if not issubclass(model, SoftDeleteModel):
            continue
        qs = model._default_manager.get_queryset()
        if getattr(qs, "_sd_mode", None) != ALIVE:
            errors.append(Error(f"{model._meta.label}: the default manager exposes soft-deleted rows.",
                                hint="Declare `objects = SoftDeleteManager()` FIRST (or use CompanyScopedSoftDeleteModel).",
                                obj=model, id="core.E001"))
        for name in ("deleted_objects", "global_objects"):
            if not hasattr(model, name):
                errors.append(Error(f"{model._meta.label} lacks the {name} manager.", obj=model, id="core.E002"))
    return errors


@register(deploy=True)
def deploy_checks(app_configs, **kwargs):
    from django.conf import settings

    errors = []
    if "django.contrib.admin" in settings.INSTALLED_APPS:
        return errors        # allowed, never required
    if getattr(settings, "AUTH_USER_MODEL", "") == "auth.User":
        errors.append(Error("core needs an email-login user model (AUTH_USER_MODEL = 'core.User').", id="core.E010"))
    return errors


def _writable(path) -> bool:
    """True when ``path`` exists and is writable, or does not exist yet but its nearest existing parent is (it will be created)."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return False
        probe = probe.parent
    return probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)


@register()
def storage_folders(app_configs, **kwargs):
    from core import conf

    errors = []
    for setting, path in (("CORE_PRIVATE_MEDIA_ROOT", conf.private_media_root()), ("CORE_BACKUP_DIR", conf.backup_dir()),
                          ("CORE_ARCHIVE_DIR", conf.archive_dir())):
        if not _writable(path):
            errors.append(Error(f"{setting}: the folder {path} cannot be created or written by this user.",
                                hint=f"Create it once as root and give it to the app user, e.g. "
                                     f"`sudo mkdir -p {path} && sudo chown -R $USER: {path}` "
                                     f"(use the service user instead of $USER under systemd), or point {setting} at a folder you own.",
                                id="core.E020"))
    return errors


@register()
def database_engines(app_configs, **kwargs):
    from django.conf import settings

    from core.backup.adapters.base import UnsupportedEngineError
    from core.backup.adapters.registry import classify_engine

    errors = []
    for alias, config in settings.DATABASES.items():
        try:
            classify_engine(config.get("ENGINE", ""))
        except UnsupportedEngineError as exc:
            errors.append(Error(f"Database '{alias}': {exc}", hint="Use PostgreSQL (recommended), MySQL 8 / MariaDB 10.6+, or SQLite.",
                                id="core.E030"))
    return errors
