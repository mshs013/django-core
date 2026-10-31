# Using `core` in another project

`core` is a reusable Django app: users and company scope, roles and permissions, CRUD, search and report builders, approvals,
internal mail, notifications, backups, PDF letterhead and the Tabler/HTMX UI. A business app (garments, accounts, HR ...) sits on
top of it and never copies it. Two rules keep this working: **`core` never imports a business app**, and a business app only
imports from `core`'s public modules.

> Status: written from the code and the dummy app in `tests/dummyapp`. Not executed here (Django could not be installed in the
> build sandbox); the contract test `tests/test_reusability.py` is the check to run first.

## 1. Add `core` to an existing Django project

**New project?** Start from this repository instead: it already contains the settings below (`config/`).

**Existing project** (Django 5.2, Python 3.12/3.13):

1. Copy the `core/` folder into the project root (next to `manage.py`).
2. Install the requirements: `pip install -r requirements.txt` (Django, django-filter, django-import-export, django-guardian,
   django-q2, reportlab, Pillow, psycopg, argon2-cffi, whitenoise). `core` has no Django admin dependency; do not install `django.contrib.admin`.
3. Settings (copy the exact blocks from `config/settings.py`; order matters):

```python
# settings.py

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party apps used by core
    "django_filters",
    "import_export",
    "guardian",
    "django_q",
    # core, then your business apps
    "core",
    "yourapp",
]

# Must be set BEFORE the first migrate of a new database.
AUTH_USER_MODEL = "core.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]

# The order matters: copy the core.* entries exactly as they appear in config/settings.py.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.core",
            ],
        },
    },
]

# Site-wide date/time formats (CORE_DATE_FORMAT, CORE_DATETIME_FORMAT, CORE_TIME_FORMAT).
FORMAT_MODULE_PATH = ["core.formats"]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
```

Also copy from `config/settings.py`: `PASSWORD_HASHERS` (Argon2), `Q_CLUSTER` (django-q2), the `STORAGES` block, session/CSRF cookie
settings, and `LOGGING` (`core.logging`). `core` has **no MEDIA_URL**: uploads live in private storage and are served only by
authorised views.

4. URLs:

```python
# project urls.py
from django.urls import include, path
urlpatterns = [path("", include("core.urls"))]       # login, dashboard, /r/<resource>/, reports, approvals, mail ...
```

5. Create the database and the first company/user:

```bash
python manage.py migrate
python manage.py bootstrap --company-name "Acme Ltd" --company-abbreviation ACME      # + DJANGO_SUPERUSER_EMAIL / _PASSWORD in the environment
python manage.py qcluster                                                              # background jobs (own terminal / service)
```

6. Every `CORE_*` option has a safe default in `core/conf.py`; override only what you need (see `docs/INSTALL.md`).

**Already have users?** Switching an existing project to `AUTH_USER_MODEL = "core.User"` after data exists is a manual migration
project (Django cannot swap the user model in place). Do it on a fresh database, or plan a data migration.

## 2. Build a business app on top

```bash
python manage.py startapp garments
```

Add it to `INSTALLED_APPS` after `core`, then:

**`garments/models.py`: inherit the abstract bases, import only from `core.models`:**

```python
from django.db import models
from core.models import AuditedModel, CompanyScopedSoftDeleteModel

class Fabric(CompanyScopedSoftDeleteModel):                    # company scoped + soft delete
    name = models.CharField(max_length=80)

class Garment(CompanyScopedSoftDeleteModel, AuditedModel):     # + audit trail + approval flags
    approval_target = "garment"
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fabric = models.ForeignKey(Fabric, null=True, blank=True, on_delete=models.PROTECT)
```

| Base class | Gives you |
|---|---|
| `CreatedInfoModel` / `UpdatedInfoModel` | created/updated by and at, filled automatically |
| `CompanyScopedModel` / `MultiCompanyScopedModel` | one company / several companies; every query is scoped, fails closed |
| `SoftDeleteModel`, `CompanyScopedSoftDeleteModel` | delete = soft delete, trash and restore |
| `AuditTrackedModel`, `AuditedModel` | old/new snapshots in the audit log; `AuditedModel` adds the approval flags |
| `CancelableModel` | a Cancel action (also after approval) |

**`garments/apps.py`: register in `ready()`** (this is the whole integration):

```python
from django.apps import AppConfig

class GarmentsConfig(AppConfig):
    name = "garments"

    def ready(self):
        from core.approvals import StepSpec, register_chain, register_target
        from core.crud.registry import register_resource
        from core.reports.registry import ReportColumn, register_report_source
        from .models import Fabric, Garment

        register_resource(Fabric, slug="fabrics", list_fields=("name",), form_fields=("name",), searchable_fields=("name",))
        register_resource(Garment, slug="garments",
                          list_fields=("name", "price", "fabric"), form_fields=("name", "price", "fabric"),
                          searchable_fields=("name",), filterable_fields=("name", "price", "fabric"))
        register_report_source("garments", Garment, label="Garments", columns=("name", "price"),
                               searchable_fields=("name",), filterable_fields=("name", "price"), default_columns=("name", "price"))
        register_chain("garment", "Garment approval", [StepSpec("merch", "Merchandiser", "garments.change_garment"),
                                                       StepSpec("manager", "Manager", "garments.approve_garment")])
        register_target("garment", Garment)
```

What one `register_resource` call gives you: list page (search, filters, sort, pagination, export), modal create / view / edit /
delete, import (with `allow_import=True`), company scope, permissions from the model's `view/add/change/delete` codenames, inline
child tables (`inlines=(Inline(...),)`), a menu entry source, and an automatic report source and query-builder model.
Every option (columns, search fields, forms, inlines, hooks, bulk actions) is explained in `docs/CRUD.md`.

Then:

```bash
python manage.py makemigrations garments && python manage.py migrate
python manage.py seed_url_permissions --dry-run     # new named URLs are denied to non-superusers until granted
python manage.py sync_approval_chains               # create the approval chain rows
```

Finally open **Menu** (Administration) and add your pages to the sidebar, and give roles the permissions they need. The menu is
stored in the database; nothing is hard-coded.

## 3. Rules to keep `core` reusable

* Business code imports `core.models`, `core.crud.registry`, `core.reports.registry`, `core.approvals`, `core.search.registry`,
  `core.mail.service`, `core.notifications.service`, `core.reports.resources.pdf` (public APIs). Never the other direction.
* No project-specific values in `core` (names, colours, tax rules). Use `CORE_*` settings, the Company record, the menu and the
  letterhead settings instead.
* Never edit files under `core/` for one project. Extend with hooks (`ResourceConfig.hooks`), skip rules, `on_approved` callbacks,
  the `core.signals.approval_event` signal, custom reports (`register_custom_report`) and your own templates.
* Keep migrations of your app in your app. Upgrading `core` = replace the folder, `migrate`.
* Run `python manage.py test tests.test_reusability --settings=config.settings_test` after every change; it fails if `core` imports
  a business app.

## 4. Upgrading `core` in a project that uses it

1. Back up the database (`manage.py backup_run --mode FULL`).
2. Replace the `core/` folder with the new version; merge any new `CORE_*` settings.
3. `pip install -r requirements.txt`, `python manage.py migrate`, `python manage.py collectstatic`.
4. `python manage.py seed_url_permissions --dry-run` (new pages), `python manage.py sync_approval_chains`, `python manage.py check --deploy`.
