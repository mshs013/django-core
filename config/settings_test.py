"""Test settings: SQLite by default (``DB_ENGINE=postgresql`` + DB_* env switches the whole suite to PostgreSQL), in-memory cache,
synchronous django-q2, and a dummy *business* app used by the reusability contract tests."""
import os

os.environ.setdefault("DJANGO_TESTING", "1")

from .settings import *  # noqa: E402,F401,F403

TESTING = True
DEBUG = False
INSTALLED_APPS = [*INSTALLED_APPS, "tests.dummyapp", "core.seeder"]
SECRET_KEY = "test-only-secret-key-" + "x" * 40
ALLOWED_HOSTS = ["testserver", "localhost"]
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CORE_PRIVATE_MEDIA_ROOT = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-private")
CORE_BACKUP_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-backups")
CORE_ARCHIVE_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "core-test-archive")
CORE_ARCHIVE_ALLOWED_TABLES = ["dummyapp_oldrecord"]

# SQLite: a real (temporary) database file instead of Django's in-memory default, so the SQLite backup/restore path is exercised.
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    import tempfile

    DATABASES["default"]["TEST"] = {"NAME": os.path.join(tempfile.gettempdir(), f"core-test-{os.getpid()}.sqlite3")}
