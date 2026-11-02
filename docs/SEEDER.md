# Demo data seeder (`core.seeder`)

Fills every registered model with realistic-looking rows so every screen can be tried (lists, search, reports, query builder,
approvals, notifications, mail, row-level access) and removes exactly those rows again. It's optional and has no other
dependencies: nothing else in `core` imports it (a test enforces that).

## Install / remove
* Dev or staging: add `"core.seeder"` to `INSTALLED_APPS` (or `DJANGO_EXTRA_APPS=core.seeder`), then `manage.py migrate`.
  `config/urls.py` mounts it at `/seed/` only when the app is installed.
* Production: leave it out of `INSTALLED_APPS`. To remove it from a database that had it: `manage.py seed_data --purge`
  (optional), then `manage.py migrate seeder zero`, then drop the app.
* Writes are refused unless `SEEDER_ENABLED` is true. It defaults to `DEBUG` (and is on in tests). `--force` overrides it
  for the command.

## Use
* **Developer tools › Seed data** (`seeder.use_seeder` permission; superusers have it). Pick the models, rows per model, new
  users and companies, the *activity* option and an optional random seed (each input is explained on the page and below). The page lists the seeded batches, and you can
  remove one batch or all of them.
* Command:
  ```
  manage.py seed_data --list                       # seedable models (dependency order) + batches
  manage.py seed_data --count 20 --users 8         # everything
  manage.py seed_data --models dummyapp.Buyer,dummyapp.Order --no-activity --seed 42
  manage.py seed_data --purge                      # remove all seeded rows (or --purge <batch>)
  ```

## Inputs (page field / command option)
| Page field | Option | Values | What it does |
| --- | --- | --- | --- |
| Rows per model | `--count N` | 1–200 on the page (default 10) | Records made for **each** ticked model. Departments and designations (shared by every company) get `N` too. Inline rows (1–4 per record) come on top, so line tables grow faster. |
| New users | `--users N` | 0–50 (default 5) | Demo people to create. `0` makes none and reuses the seeded users already in the database (role "Seed: business access"); without any, the first active users are used for activity. |
| New companies | `--companies N` | 0–10 (default 0) | Extra companies. Existing active companies are always used too; one is made when there are none. |
| Random seed | `--seed N` | any whole number, e.g. `1`, `42`, `-7`, `2026`; empty = random | Fixes the random generator so a run can be replayed: the same seed on the same starting database gives the same names, amounts, dates, choices and FK picks. Codes and e-mails continue after the rows already in the tables, so a second run with the same seed on a database that already holds the first is similar but not identical. Use it to reproduce a bug or rebuild the same demo after a purge. Anything that is not a whole number is refused on the page. |
| Activity | `--no-activity` turns it off | on / off | Submits about 60 % of approvable records for approval, decides some steps (raising notifications) and sends up to 10 internal mails between seeded users. |
| Model ticks | `--models a.B,c.D` | default: all seedable models | Which models to fill. Users/companies tick boxes decide whether users and companies are seeded at all. |

Examples: `--count 50` gives 50 departments, 50 designations and 50 rows of every business model; `--count 10 --users 0`
gives 10 of each and no new users.

## What it does
1. **Companies.** Existing ones are reused, and `--companies N` adds more (one is made when none exist). Then `--count`
   departments and designations are made (they belong to no company).
2. **Users** (password `demo-pass-12345`). Each gets a profile in 1–2 companies, the role **"Seed: business access"**
   (every permission of the seeded business models), and *all rows* object access for each access-scoped model.
3. **Every model registered with `register_resource`**, except core's configuration and log models. Parents are filled
   before children. Company-scoped rows take turns across the companies, and FK picks stay inside the same company.
   Inlines are filled for every record, including nested ones, with `position_field` numbered.
4. **Activity** (optional). About 60 % of approvable records are submitted by seeded users, and some steps are approved
   or rejected, which also raises notifications. A few internal mails are sent between seeded users.

Values are guessed from the field type and name (`email`, `phone`, `address`, `qty`, `price`, `*_no`, choices, min/max
validators, `max_length`…). Each row is checked with `full_clean()`. A row that clashes on unique or validation rules is
retried with new values.

Every created row (including approval requests, mails and notifications) is listed in `SeedRecord` under a batch id. The
purge deletes them newest first: soft-delete models are hard-deleted, and rows still referenced by real data are kept and
reported.

## Steering it from an app
Only do this when the seeder is installed, so your app still works without it:

```python
from django.apps import apps
if apps.is_installed("core.seeder"):
    from core.seeder import register_seeder
    register_seeder(Order, count=30, fields={"ref": lambda ctx, n: f"PO-{n:05d}", "status": "draft"})
    register_seeder(Attachment, skip=True)                      # never seed
    register_seeder(Shipment, factory=lambda ctx, n: Shipment.objects.create(...))   # full control
```

`ctx` gives `ctx.rng` (seeded `random.Random`), `ctx.company()`, `ctx.user(company)`, `ctx.pick(Model, company)` and
`ctx.serial(Model)`. If a model can't be filled (a required file field, or a required FK to a model with no rows), it's
skipped and the reason is shown. Register a `factory` for it.
