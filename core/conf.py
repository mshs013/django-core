"""``CORE_*`` settings with safe defaults. Read through :func:`get` so a project only overrides what it needs."""
from __future__ import annotations

from django.conf import settings

DEFAULTS = {
    "CORE_MAX_PAGE_SIZE": 100,
    "CORE_DEFAULT_PAGE_SIZE": 25,
    "CORE_MAX_SEARCH_VALUE_LENGTH": 200,
    "CORE_MAX_SEARCH_ROWS": 25,
    "CORE_IDLE_TIMEOUT_SECONDS": 1800,
    "CORE_LOGIN_MAX_ATTEMPTS": 5,
    "CORE_LOGIN_LOCKOUT_SECONDS": 900,
    "CORE_ENFORCE_URL_PERMISSIONS": True,
    "CORE_PUBLIC_URL_NAMES": ("core:login", "core:logout", "core:password_reset", "core:password_reset_done",
                              "core:password_reset_confirm", "core:password_reset_complete", "core:health", "core:ready"),
    "CORE_PERMISSION_CACHE_SECONDS": 300,
    "CORE_MENU_CACHE_SECONDS": 300,
    "CORE_MENU_MAX_DEPTH": 6,                  # deepest menu level (1 = top level); the editor refuses deeper, the sidebar stops there
    "CORE_MENU_AUTO_RESOURCES": False,         # True: also list every registered resource under an automatic "Data" group
    "CORE_PREVIEW_ROWS": 50,
    "CORE_QUERY_AUTO_RESOURCES": True,         # every plain CRUD resource can be used in the query builder
    "CORE_QUERY_MAX_DEPTH": 3,                 # joins from the root model (max 5)
    "CORE_QUERY_MAX_FIELDS": 1500,             # fields offered in the query builder's pickers
    "CORE_CHART_MAX_POINTS": 200,              # rows drawn in a report chart
    "CORE_M2M_WIDGET": "select",               # how many-to-many fields render in generated forms: "select" | "checkbox"
    "CORE_DATE_FORMAT": "d-M-Y",               # site-wide display formats (Django date-filter syntax), see core.formatting
    "CORE_DATETIME_FORMAT": "d-M-Y h:i:s A",
    "CORE_TIME_FORMAT": "h:i:s A",
    "CORE_AUTO_REPORT_SOURCES": True,          # every list page without its own report source gets an automatic one (r-<slug>)
    "CORE_EXPORT_SYNC_MAX_ROWS": 5000,
    "CORE_EXPORT_EXPIRY_HOURS": 24,
    "CORE_EXPORT_MAX_ROWS": 100000,
    "CORE_PRIVATE_MEDIA_ROOT": None,          # default: <BASE_DIR>/private_media (never served by the web server)
    "CORE_PDF_MAX_ROWS": 5000,
    "CORE_PDF_LETTERHEAD": {},                # fallbacks for fields the active company leaves blank (name, address_lines, logo, brand, from_company)
    "CORE_PDF_LETTERHEAD_CLASS": "",          # dotted path to a LetterheadTemplate subclass ("" = the default layout)
    "CORE_IMPORT_MAX_ROWS": 5000,
    "CORE_UPLOAD_MAX_BYTES": 10 * 1024 * 1024,
    "CORE_UPLOAD_ALLOWED_EXTENSIONS": (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".csv", ".xlsx", ".docx", ".odt", ".ods"),
    "CORE_IMAGE_EXTENSIONS": (".png", ".jpg", ".jpeg", ".gif", ".webp"),
    "CORE_ORPHAN_GRACE_HOURS": 24,
    "CORE_SELF_APPROVAL": False,
    "CORE_NOTIFY_EMAIL": False,
    "CORE_CHANNELS_LIVE": False,
    "CORE_BACKUP_DIR": None,                  # default: <BASE_DIR>/var/backups
    "CORE_ARCHIVE_DIR": None,                 # default: <BASE_DIR>/var/archive
    "CORE_BACKUP_TIMEOUT_SECONDS": 3600,
    "CORE_BACKUP_COMPRESSION": 6,
    "CORE_BACKUP_KEEP_DAYS": 30,
    "CORE_BACKUP_MIN_KEEP": 3,
    "CORE_BACKUP_TOOLS_DIR": "",
    "CORE_BACKUP_ALLOW_RESTORE": False,
    "CORE_BACKUP_FILE_ROOTS": (),             # directories offered by the file backup; empty = project, static, media, private media
    "CORE_BACKUP_FILE_EXCLUDES": ("*.pyc", "__pycache__", ".git", "*.tmp", ".venv", "venv", "node_modules"),
    "CORE_ARCHIVE_ALLOWED_TABLES": (),
    "CORE_ARCHIVE_MIN_AGE_DAYS": 30,
    "CORE_ARCHIVE_BATCH_SIZE": 1000,
    "CORE_JOBS_KEEP_DAYS": 30,                # background-job result files older than this are deleted (rows stay); 0 = keep all
    "CORE_JOBS_MIN_KEEP": 3,                  # ... but the newest N files per job type always stay
    "CORE_LOG_REDACT_KEYS": (),
}


def get(name: str):
    return getattr(settings, name, DEFAULTS[name])


def base_dir():
    from pathlib import Path

    return Path(getattr(settings, "BASE_DIR", "."))


def _dir(name: str, *default):
    """The folder configured in ``name`` (a relative path is taken from the project folder, not the shell's current folder)."""
    from pathlib import Path

    value = get(name)
    path = Path(value).expanduser() if value else base_dir().joinpath(*default)
    return path if path.is_absolute() else base_dir() / path


def private_media_root():
    return _dir("CORE_PRIVATE_MEDIA_ROOT", "private_media")


def backup_dir():
    return _dir("CORE_BACKUP_DIR", "var", "backups")


def archive_dir():
    return _dir("CORE_ARCHIVE_DIR", "var", "archive")
