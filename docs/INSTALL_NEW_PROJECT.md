# Using `core` in a new Django project

`core` is a drop-in app. This is the minimal setup; `config/settings.py` is the complete, environment-driven example.
Verified on 2026-10-29 with a throwaway project (SQLite, Django 5.2.18): `check`, `migrate`, `bootstrap`, a second `migrate`
and `makemigrations --check` all clean, 26 default menu rows created.

## 1. Requirements
Python 3.12+, Django 5.2 LTS and the pins in `requirements.txt` (`pip install -r requirements.txt`). Copy the `core/` directory into
the project (it must be importable as `core`).

## 2. Settings
```python
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_filters", "import_export", "guardian", "django_q",
    "core",                       # after the libraries above, BEFORE your business apps
    # "knitting", ...
]
# No django.contrib.admin: core refuses it (system check).

AUTH_USER_MODEL = "core.User"     # set this BEFORE the very first `migrate`; it cannot be changed afterwards
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", "guardian.backends.ObjectPermissionBackend"]
ANONYMOUS_USER_NAME = None
LOGIN_URL, LOGIN_REDIRECT_URL = "core:login", "core:dashboard"
FORMAT_MODULE_PATH = ["core.formats"]

MIDDLEWARE = [                     # order matters
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",                     # optional (static files without nginx)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "core.middleware.RequestContextMiddleware",
    "core.middleware.IdleTimeoutMiddleware",
    "core.middleware.UrlPermissionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {"context_processors": [
    "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages", "core.context_processors.core"]}}]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
Q_CLUSTER = {"name": "myproject", "orm": "default", "timeout": 300, "retry": 360}   # django-q2 on the database
```
Secrets and hosts come from the **environment only** (never in source): `DJANGO_SECRET_KEY`, `DB_*`, `DJANGO_ALLOWED_HOSTS`,
`CORE_PRIVATE_MEDIA_ROOT` … — see `docs/INSTALL.md` §3 for the full list. Every `CORE_*` setting is optional (defaults in
`core/conf.py`).

## 3. URLs
```python
from django.urls import include, path
urlpatterns = [path("", include("core.urls"))]
handler403, handler404, handler500 = ("core.views.dashboard.error_403", "core.views.dashboard.error_404",
                                      "core.views.dashboard.error_500")
```

## 4. Database, first company, first user
```bash
python manage.py migrate                 # core.0001_initial + the default menu (post_migrate) + permissions
python manage.py bootstrap --company-name "My Company" --company-abbreviation MC
#   creates the superuser when DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD are set in the environment; otherwise:
python manage.py createsuperuser
python manage.py check
```
`core` ships no default credentials and no company details (PDF letterheads come from the active company's record).

## 5. Business apps
Inherit `core`'s base models (`core.models.CompanyScopedModel` …) and register resources from `AppConfig.ready()` (README example).
Their migrations depend on **`("core", "0001_initial")`**; `makemigrations` adds that dependency automatically.
