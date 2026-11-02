"""Settings shared by every environment, with production-safe defaults. Pick a module, not this file:
``config.settings.production`` (servers), ``config.settings.dev`` (a developer machine), ``config.settings.test`` (the test suite).
``manage.py`` / ``wsgi.py`` / ``asgi.py`` choose one by themselves (``config.env.settings_module``).

Everything environment-specific comes from environment variables (or ``.env``); nothing secret lives in source.
The ``core`` app is configured through ``CORE_*`` settings (see ``core/conf.py`` for the defaults)."""
from __future__ import annotations

from config.env import BASE_DIR, env, env_bool, env_list, load_dotenv

load_dotenv()

DEBUG = False
TESTING = False

SECRET_KEY = env("DJANGO_SECRET_KEY", "")                               # required by production.py; dev/test fill in a local key

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_filters",
    "import_export",
    "guardian",
    "django_q",
    "core",
]
# ``django.contrib.admin`` is deliberately NOT installed: core has no admin dependency.
# Business apps are appended by the project (e.g. INSTALLED_APPS += ["garments"]).
INSTALLED_APPS += env_list("DJANGO_EXTRA_APPS")

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", "guardian.backends.ObjectPermissionBackend"]
ANONYMOUS_USER_NAME = None                                              # guardian: no anonymous user row
LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:dashboard"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",          # default-deny: views opt out with @login_not_required
    "core.middleware.RequestContextMiddleware",
    "core.middleware.IdleTimeoutMiddleware",
    "core.middleware.UrlPermissionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context_processors.core",
    ], "builtins": []},
}]

# ---- database: PostgreSQL primary (requirements-postgresql.txt), MySQL 8 / MariaDB 10.6+ optional (requirements-mysql.txt),
# SQLite for tests / small installs ------------------------------------------------------------------------------------------------
def database_config(default_engine: str) -> dict:
    """The ``default`` database from ``DB_ENGINE`` (``postgresql`` / ``mysql`` / ``mariadb`` / ``sqlite``) and the ``DB_*`` variables."""
    engine = env("DB_ENGINE", default_engine)
    if engine == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": env("DB_NAME", str(BASE_DIR / "db.sqlite3")),
                "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20}}
    if engine in ("mysql", "mariadb"):
        return {"ENGINE": "django.db.backends.mysql", "NAME": env("DB_NAME", "core"), "USER": env("DB_USER", "core"),
                "PASSWORD": env("DB_PASSWORD", ""), "HOST": env("DB_HOST", "localhost"), "PORT": env("DB_PORT", "3306"),
                "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
                "OPTIONS": {"charset": "utf8mb4", "connect_timeout": 10, "isolation_level": "read committed",
                            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"},
                "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"}}
    return {"ENGINE": "django.db.backends.postgresql", "NAME": env("DB_NAME", "core"), "USER": env("DB_USER", "core"),
            "PASSWORD": env("DB_PASSWORD", ""), "HOST": env("DB_HOST", "localhost"), "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")), "OPTIONS": {"connect_timeout": 10}}


def silenced_checks(databases: dict) -> list[str]:
    # MySQL/MariaDB have no partial indexes: "unique among live rows" (soft delete) is enforced by model validation only, and
    # django-q's conditional index is a plain index there.
    return ["models.W036", "models.W037"] if databases["default"]["ENGINE"].endswith("mysql") else []


DATABASES = {"default": database_config("postgresql")}
SILENCED_SYSTEM_CHECKS = silenced_checks(DATABASES)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher", "django.contrib.auth.hashers.Argon2PasswordHasher"]

# ---- sessions / cookies / transport security -------------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", str(60 * 60 * 12)))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False                       # htmx reads the token from the page, not the cookie; keep default semantics
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if env_bool("DJANGO_BEHIND_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REDIRECT_EXEMPT = [r"^health/$", r"^ready/$"]

# ---- cache (shared between workers: permission/menu versioning and login throttling rely on it) ----------------------------
CACHES = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                      "LOCATION": str(BASE_DIR / env("CACHE_DIR", "var/cache")), "TIMEOUT": 300}}
if env("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}

# ---- i18n / static / media -------------------------------------------------------------------------------------------------
LANGUAGE_CODE = "en"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True
# Site-wide display formats (Django date-filter syntax). core.formats puts them in front of the locale's own formats.
FORMAT_MODULE_PATH = ["core.formats"]
CORE_DATE_FORMAT = env("CORE_DATE_FORMAT", "d-M-Y")                    # 21-Oct-2026
CORE_DATETIME_FORMAT = env("CORE_DATETIME_FORMAT", "d-M-Y h:i:s A")    # 21-Oct-2026 06:42:46 AM
CORE_TIME_FORMAT = env("CORE_TIME_FORMAT", "h:i:s A")                  # 06:42:46 AM
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").is_dir() else []
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
# No MEDIA_URL: uploads live in PRIVATE storage (core.files) and are served only by authorised views.

# ---- email -----------------------------------------------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "25"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@example.invalid")

# ---- django-q2 (database broker: no Redis required; Q_SYNC=1 runs jobs inline, the test settings always do) ------------------
Q_CLUSTER = {"name": "core", "workers": int(env("Q_WORKERS", "2")), "timeout": int(env("Q_TIMEOUT", "3600")), "retry": int(env("Q_RETRY", "3700")),
             "queue_limit": 50, "bulk": 10, "orm": "default", "catch_up": False, "sync": env_bool("Q_SYNC", False),
             "ack_failures": True, "max_attempts": 1}

# ---- logging: structured JSON lines, never credentials / tokens / hashes / file contents ---------------------------------------
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.logging.JsonFormatter"}},
    "filters": {"redact": {"()": "core.logging.RedactFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["redact"]}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# ---- core ----------------------------------------------------------------------------------------------------------------------
CORE_BACKUP_DIR = env("CORE_BACKUP_DIR", "") or None
CORE_ARCHIVE_DIR = env("CORE_ARCHIVE_DIR", "") or None
CORE_PRIVATE_MEDIA_ROOT = env("CORE_PRIVATE_MEDIA_ROOT", "") or None
CORE_BACKUP_ALLOW_RESTORE = env_bool("CORE_BACKUP_ALLOW_RESTORE", False)
CORE_BACKUP_FILE_ROOTS = env_list("CORE_BACKUP_FILE_ROOTS")
CORE_ARCHIVE_ALLOWED_TABLES = env_list("CORE_ARCHIVE_ALLOWED_TABLES")
CORE_NOTIFY_EMAIL = env_bool("CORE_NOTIFY_EMAIL", False)
CORE_MENU_MAX_DEPTH = int(env("CORE_MENU_MAX_DEPTH", "6"))             # deepest sidebar menu level (1 = top level)
