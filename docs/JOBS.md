# Background jobs (`core.jobs`)

Long work a business app needs to do outside the request (recalculating costs, a big import, a nightly summary, a file to build)
runs as a **background job**. `core` gives every app the same pieces: an allow-list of handlers, a history row per run with
progress and errors, a result file to download, retry, retention, a page and a bell notice. Backups, archive runs and exports keep
their own pages; this is for everything an app adds.

## 1. Register a handler

In your app's `registrations.py` (imported from `AppConfig.ready()`, like resources and reports):

```python
from core.jobs import register_job

@register_job("knitting.recalc_costs", label="Recalculate yarn costs", timeout=1800)
def recalc_costs(job):
    styles = Style.objects.filter(company_id=job.company_id)        # job.company = the company it was started in
    for i, style in enumerate(styles, 1):
        ...
        job.set_progress(i * 100 // len(styles), f"{i} of {len(styles)} styles")
    job.save_file("yarn-costs.csv", build_csv(styles))              # optional: offered as Download on the jobs page
    return {"styles": len(styles)}                                  # stored as job.result (small, JSON-safe)
```

| Option | Default | Meaning |
|---|---|---|
| `name` | — | Unique id, conventionally `"<app>.<what>"` (max 150 chars). Stored on each run. |
| `label` | the name | What the page and the bell notice show. A run can override it (`submit_job(label=...)`). |
| `timeout` | 3600 | Seconds before the django-q2 worker stops the run. |
| `allow_retry` | `True` | Whether a failed run may be started again (never automatic). Turn off for work that must not repeat. |
| `description` | `""` | Free text shown by `manage.py jobs_info`. |

Only registered names ever run. A stored run whose type is no longer registered (its app was removed) fails with
"No handler is registered" instead of executing anything.

The handler gets the `JobRecord`: `job.params` (what the run was started with), `job.company_id`, `job.created_by`,
`job.set_progress(percent, message)`, `job.save_file(name, bytes)`. Raise `core.jobs.JobError("message")` for a failure whose
message the user should read; any other exception is stored as `Type: message`. Both are redacted (passwords, tokens) before saving.

## 2. Start a run

```python
from core.jobs import submit_job

job = submit_job("knitting.recalc_costs", {"season": "SS27"}, user=request.user, company=ctx.company)
messages.info(request, "Started: you get a notice when it is done.")
return redirect("core:job_detail", pk=job.pk)
```

`submit_job` saves the row (status *Queued*) and queues it when the current transaction commits. Parameters must be JSON-safe; keys
that look like secrets (`password`, `token`, `api_key`, …) are masked before saving, so never pass credentials. Check your own
permission before calling it: `submit_job` does not decide who may start what.

## 3. What happens next

1. A `qcluster` worker (or inline with `Q_SYNC=1`, and in tests) picks the run up. A run is claimed atomically from *Queued*, so
   a duplicate delivery never runs it twice; `attempts` counts the runs.
2. The handler's return value becomes `result` (*Done*, 100 %), or the error is stored (*Failed*).
3. The person who started it gets a bell notification that opens the run's page (`/jobs/<id>/`).

## 4. The page and who sees what

| Who | Sees | May |
|---|---|---|
| Anyone signed in | the runs they started (`/jobs/`, also from the bell notice) | download their result files, **Run again** their failed runs |
| `core.view_jobrecord` (staff by default; menu entry) | everybody's runs, *Only mine* filter, *Started by* column | download any result file |
| `core.retry_jobrecord` | — | **Run again** anybody's failed run |

## 5. Retention and commands

Result files older than `CORE_JOBS_KEEP_DAYS` (30; `0` keeps all) are deleted, but the newest `CORE_JOBS_MIN_KEEP` (3) per job type
always stay. Rows are never deleted: the history survives, the page shows "removed (retention)". It runs in the daily
`core.tasks.jobs.housekeeping` task or with `python manage.py jobs_retention [--dry-run]`.

`python manage.py jobs_info` lists every registered type with its run counts, and warns about stored runs whose type is not
registered any more.

## 6. Example in this repository

`tests/dummyapp/registrations.py` registers `dummyapp.garment_price_list` (CSV of the active company's garments);
`tests/test_jobs.py` covers runs, redaction, the allow-list, retry, retention, the commands, the pages and the upgrade migration.
