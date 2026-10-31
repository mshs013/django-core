# core — a reusable Django ERP foundation

One reusable Django app, **`core`**, plus a thin **`config`** project package. Nothing else exists as a Python package in the
deliverable (`tests/` holds the test-suite and a dummy business app used only by the tests). Every feature lives inside `core`:
identity, companies, permissions, audit, CRUD/search/report/approval engines, menu, notifications, mail, files, background jobs,
backup, archive.

* **No Django admin.** `django.contrib.admin` is not installed and `core` never references it.
* **No 2FA.** No TOTP, no OTP package, no stub. Authentication is e-mail + password with throttling, idle timeout and audit.
* **Server-rendered** Tabler templates + HTMX + Alpine (vendored, no CDN, no Node build, no Docker).
* **PostgreSQL** primary, **MySQL 8 / MariaDB 10.6+** optional (`DB_ENGINE=mysql`), SQLite for tests/small installs.

> **Tested.** Django 5.2.18 LTS, Python 3.12+. The full test suite passes on SQLite, PostgreSQL 17, MySQL 8.0 and MariaDB 11.8
> (on Windows 7 backup/file tests need symlink rights a normal account lacks). Details and coverage: `docs/VALIDATION.md`,
> `docs/COVERAGE.md`.

**Installing:** follow [`docs/INSTALL.md`](docs/INSTALL.md) (development and production, every environment variable, troubleshooting).

## Quick install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # runtime only; requirements-dev.txt adds test/audit tools
pip install -r requirements-postgresql.txt   # database driver (MySQL / MariaDB: requirements-mysql.txt; SQLite: none)
# create .env with DJANGO_SECRET_KEY, DB_* (see docs/INSTALL.md section 3); never commit .env
set -a; . ./.env; set +a
python manage.py migrate
python manage.py bootstrap --company-name "Acme" --company-abbreviation ACME   # idempotent; superuser from env vars, no default credentials
python manage.py collectstatic --noinput
python manage.py runserver                # development only; see docs/DEPLOYMENT.md for gunicorn
python manage.py qcluster                 # background worker (exports, backups, archive, registered jobs)
```

Run the tests: `python manage.py test` (SQLite; `manage.py test` always uses `config.settings.test`) or
`DB_ENGINE=postgresql DB_NAME=... DB_USER=... DB_PASSWORD=... python manage.py test` (PostgreSQL).

Settings live in `config/settings/` (`base`, `production`, `dev`, `test`); `DJANGO_DEBUG=1` in `.env` selects `dev`, otherwise
`production` is used (docs/INSTALL.md, *Settings modules*).
Pure-Python tests (no Django needed):
`python -m unittest tests.test_adapters_pure tests.test_backup_pure tests.test_codec tests.test_files tests.test_redaction_pure`.

## Drop `core` into another project

1. `pip install "<path to this repo or git url>[postgres,server]"` (`pyproject.toml`; extras: `postgres` = psycopg, `server` =
   whitenoise / argon2 / gunicorn, `dev` = test tools), or copy the `core/` directory and `pip install -r requirements.txt` plus the driver file
   (`requirements-postgresql.txt` or `requirements-mysql.txt`).
2. `INSTALLED_APPS += ["django_filters", "import_export", "guardian", "django_q", "core"]`, `AUTH_USER_MODEL = "core.User"`,
   `AUTHENTICATION_BACKENDS` with guardian, the middleware block from `config/settings/base.py` (order matters) and
   `FORMAT_MODULE_PATH = ["core.formats"]` (site-wide date format, `CORE_DATE_FORMAT` …).
3. `path("", include("core.urls"))`, then `migrate` and `bootstrap`.
4. Override any `CORE_*` setting (defaults in `core/conf.py`); nothing is required.

## Example: a garments app on top of `core`

`garments/models.py` — inherit the abstract bases; never import anything from the project:

```python
from django.db import models
from core.models import AuditedModel, CompanyScopedSoftDeleteModel

class Fabric(CompanyScopedSoftDeleteModel):
    name = models.CharField(max_length=80)

class Garment(CompanyScopedSoftDeleteModel, AuditedModel):        # company scoped + soft delete + audit + approvals
    approval_target = "garment"
    name = models.CharField(max_length=80)
    style_no = models.CharField(max_length=30)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=[("draft", "Draft"), ("ready", "Ready")], default="draft")
    fabric = models.ForeignKey(Fabric, null=True, blank=True, on_delete=models.PROTECT)
    class Meta:
        permissions = [("approve_garment", "Can approve garments")]

class GarmentLine(models.Model):                                    # child rows edited inside the garment form
    garment = models.ForeignKey(Garment, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=80)
    qty = models.PositiveIntegerField(default=1)
    position = models.PositiveIntegerField(default=0)               # NOT unique: row order
```

`garments/apps.py`:

```python
class GarmentsConfig(AppConfig):
    name = "garments"
    def ready(self):
        from . import registrations      # registries are filled in ready(), never at import time of core
```

`garments/registrations.py` — everything the UI needs, declared once:

```python
from core.approvals import StepSpec, register_chain, register_target
from core.crud.registry import Inline, register_resource
from core.reports.registry import ReportColumn, register_report_source
from .models import Fabric, Garment, GarmentLine

register_resource(Fabric, slug="fabrics", list_fields=("name",), form_fields=("name",), searchable_fields=("name",))
register_resource(
    Garment, slug="garments",
    list_fields=("name", "style_no", "price", "status", "fabric"),
    form_fields=("name", "style_no", "price", "status", "fabric"),
    searchable_fields=("name", "style_no"),                                   # quick search
    filterable_fields=("name", "style_no", "price", "status", "fabric"),      # advanced builder allowlist
    inlines=(Inline(GarmentLine, fields=("description", "qty", "position"), slug="lines", position_field="position",
                   hard_delete=True),),                                   # plain child rows: removable only when opted in
    allow_import=True)
register_report_source(
    "garments", Garment, label="Garments",
    columns=[ReportColumn("status", "Status", "status", "choice", groupable=True, aggregates=("count",)),
             ReportColumn("price", "Price", "price", "number", aggregates=("sum", "avg", "min", "max"))],
    filterable_fields=("name", "status", "price"), default_columns=("status", "price"))
register_chain("garment", "Garment approval",
               [StepSpec("merch", "Merchandiser", "garments.change_garment"), StepSpec("manager", "Manager", "garments.approve_garment")])
register_target("garment", Garment)
```

After `migrate`, `manage.py sync_approval_chains` and `manage.py seed_url_permissions`, the app has — with no more code —
list/detail/form/delete/trash/restore pages with company scoping, quick + advanced + saved searches, multi-row inline editing,
CSV/XLSX/…/PDF export and import (preview then confirm), a report builder, an approval workflow with history, audit rows and menu entries.
The working version of exactly this lives in `tests/dummyapp/` and is exercised by `tests/test_crud.py`, `test_search.py`,
`test_reports.py`, `test_approvals.py`.

## Documents

`docs/INSTALL.md` (install, settings, troubleshooting) · `docs/INSTALL_UBUNTU.md` (`install.py` on Ubuntu) ·
`docs/DEPLOYMENT.md` (production) · `docs/UPGRADING.md` (migrations, upgrades, Django version policy) ·
`docs/ARCHITECTURE.md` · `docs/FEATURE_MATRIX.md` · `docs/VALIDATION.md` (how it is tested) · `docs/COVERAGE.md` ·
`core/static/core/vendor/VENDORED.md` (front-end libraries)

Feature guides (what it does, how to use it, how to extend it):
`docs/SEARCH_BUILDER.md` (dynamic search builder) · `docs/REPORT_BUILDER.md` (report builder, custom reports, PDF letterhead) ·
`docs/QUERY_BUILDER.md` (custom query/report builder: joins, functions, grouping, charts) ·
`docs/APPROVALS.md` (dynamic approval system) · `docs/MAIL.md` (internal mailing system) ·
`docs/ACCESS_CONTROL.md` (roles, permissions, object access, user access, tabbed forms) ·
`docs/MENU_BUILDER.md` (sidebar menu: builder, defaults, code entries) · `docs/MENUS.md` (every default menu entry) ·
`docs/AUDIT.md` (what is audited) · `docs/SEEDER.md` (optional demo-data app) ·
`docs/JOBS.md` (background jobs for business apps: `register_job`, `submit_job`, Background jobs page)

## Default menu

The default navigation (Dashboard, an *Administration* group, Approvals, Reports, Mail, Notifications) is stored
as `MenuItem` rows,
created by `python manage.py migrate` (a `post_migrate` receiver fills an empty menu) and re-creatable with `python manage.py seed_default_menu` (adds only what is missing).
Inside Administration, staff see Users, Common › Departments / Designations, Auth, Notification Rules, Backups, Archive, Export jobs,
Background jobs and Audit log; Companies, Menu Builder, Approval Builder, Saved Reports and Search builder are for superusers. Every menu is
described page by page in [docs/MENUS.md](docs/MENUS.md). Edit, reorder, hide or delete rows at `/menu/`. Set `CORE_MENU_AUTO_RESOURCES = True` to also list every registered resource automatically.
