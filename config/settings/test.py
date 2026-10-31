"""Test settings (``manage.py test`` picks them by itself): SQLite by default (``DB_ENGINE=postgresql`` / ``mysql`` + DB_* env
switches the whole suite to that database), in-memory cache, synchronous django-q2, and a dummy *business* app used by the
reusability contract tests."""
import os
import tempfile

from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS, Q_CLUSTER, database_config, silenced_checks

TESTING = True
DEBUG = False
INSTALLED_APPS = [*INSTALLED_APPS, "tests.dummyapp", "core.seeder"]
SECRET_KEY = "test-only-secret-key-" + "x" * 40
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": database_config("sqlite")}
SILENCED_SYSTEM_CHECKS = silenced_checks(DATABASES)
# SQLite: a real (temporary) database file instead of Django's in-memory default, so the SQLite backup/restore path is exercised.
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["TEST"] = {"NAME": os.path.join(tempfile.gettempdir(), f"core-test-{os.getpid()}.sqlite3")}

SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
Q_CLUSTER = {**Q_CLUSTER, "sync": True}

CORE_PRIVATE_MEDIA_ROOT = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-private")
CORE_BACKUP_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-backups")
CORE_ARCHIVE_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-archive")
CORE_ARCHIVE_ALLOWED_TABLES = ["dummyapp_oldrecord"]
