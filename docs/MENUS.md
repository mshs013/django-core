# Menus — what every page does

One section per entry of the default sidebar: who sees it, what is on the page, how to do the everyday jobs, and where the deeper
documentation is. Developers: menu rows live in the database (see [MENU_BUILDER.md](MENU_BUILDER.md)); the defaults are
`core/menu/defaults.py`, and the "staff" rights below come from `STAFF_IMPLIED` in `core/permissions/service.py`.

## Contents
* [Who sees what](#who-sees-what)
* [Pages shared by every list](#pages-shared-by-every-list) (search, export, import, trash)
* [Dashboard](#dashboard)
* [Administration](#administration)
  * [Common › Companies](#companies) · [Departments](#departments) · [Designations](#designations)
  * [Users](#users) · [Menu Builder](#menu-builder)
  * [Approval Builder › Approval Types](#approval-types) · [Approval Steps](#approval-steps) · [Approval Requests](#approval-requests)
  * [Auth › Permissions](#permissions) · [Roles](#roles)
  * [Notification Rules](#notification-rules) · [Saved Reports](#saved-reports) · [Search builder](#search-builder)
  * [Backups](#backups) · [Archive](#archive) · [Export jobs](#export-jobs) · [Background jobs](#background-jobs) · [Audit log](#audit-log)
* [Approvals](#approvals) · [Reports](#reports) · [Mail](#mail) · [Notifications](#notifications)
* [Developer tools › Seed data](#developer-tools--seed-data)
* [Top bar](#top-bar) (sidebar fold, theme, mail, bell, account menu › Profile / Change password / Sign out, company switcher)

---

## Who sees what

```
├── Dashboard                                  everyone signed in
├── Administration                             staff & superusers
│   ├── Common                                 staff & superusers
│   │   ├── Companies                          superusers
│   │   ├── Departments                        staff & superusers
│   │   └── Designations                       staff & superusers
│   ├── Users                                  staff & superusers
│   ├── Menu Builder                           superusers
│   ├── Approval Builder                       superusers
│   │   ├── Approval Types                     superusers
│   │   ├── Approval Steps                     superusers
│   │   └── Approval Requests                  superusers
│   ├── Auth                                   staff & superusers
│   │   ├── Permissions                        staff & superusers
│   │   └── Roles                              staff & superusers
│   ├── Notification Rules                     staff & superusers
│   ├── Saved Reports                          superusers
│   ├── Search builder                         superusers
│   ├── Backups                                staff & superusers
│   ├── Archive                                staff & superusers
│   ├── Export jobs                            staff & superusers
│   ├── Background jobs                        staff & superusers (everyone sees their own runs at /jobs/)
│   └── Audit log                              staff & superusers
├── Approvals                                  core.view_approvalrequest
├── Reports                                    core.view_reportdefinition
├── Mail                                       core.view_mailmessage
├── Notifications                              core.view_notification
└── Developer tools                            only when the seeder app is installed
    └── Seed data                              seeder.use_seeder (superusers)

Top bar (not in the Menu Builder): sidebar fold · company logo/name · theme · Mail · bell ·
account menu (Profile, Change password, Sign out) · company switcher       everyone signed in
```

* **Superuser** — the *Superuser* flag on the user (only a superuser can set it). Sees and may do everything.
* **Staff** — the *Staff* flag (only a superuser can set it). Staff hold the rights of the "staff & superusers" pages without any
  role: users (view / add / change), departments, designations and notification rules (view / add / change / delete), backups
  (view, create, download, validate), archive (view, run) and their own export jobs. **Restoring** a backup or an archive and
  **deleting** backup files stay with superusers.
* The four everyday pages (Approvals, Reports, Mail, Notifications) need their permission, normally given through a role.
* Hiding a menu entry never grants or removes access: the page itself checks the permission (typing the URL gives *403*).
* An administrator can change any of this in the [Menu Builder](#menu-builder) — e.g. untick *Superuser only* on Companies and
  give a role `core.view_company`.

---

## Pages shared by every list

Most Administration pages are lists built by the same CRUD engine, so they work alike:

| Part | What it does |
|---|---|
| **Search bar** | Quick search over the page's text columns; **Filter** opens the filter builder (field, operator, value; AND / OR groups). **Save** keeps the search (personal); *Standard* searches made in the [Search builder](#search-builder) appear for everyone. See [SEARCH_BUILDER.md](SEARCH_BUILDER.md). |
| **Columns** | Click a heading to sort (only sortable columns). Image columns show a thumbnail. |
| **New** | Opens the form in a pop-up (tabbed for bigger forms; a red dot marks a tab with errors). |
| **Row** | Click to open the record; **Edit**, **Delete** (soft delete where the model supports it — the row moves to **Trash** and can be restored). |
| **Export** | Excel / CSV / PDF of the rows you can see (with the current search). Above `CORE_EXPORT_SYNC_MAX_ROWS` (5 000) rows the export runs in the background and appears under [Export jobs](#export-jobs). |
| **Import** | Only where switched on (Companies, Departments, Designations). Download the empty template, fill it, upload: a dry-run preview shows what would change; **Confirm** writes it in one transaction. Company and created/updated stamps are never read from the file. |
| **Detail page** | All fields, related lists, the audit history and, for approvable records, the approval card, signature row and PDF / Excel buttons. |

Every list is limited to the **active company** (top-bar switcher), then to the user's row-level access, then to their permissions.

---

## Dashboard
**Who:** everyone signed in. **Where:** `/`.

Counters for *Awaiting your approval*, *My open requests* and *Unread notifications*, each linking to its page. It is the landing page after
sign-in and after a company switch that the current page does not allow.

---

## Administration
A group (no page of its own). Visible to staff and superusers; inside it every entry follows the table above.

### Companies
**Who:** superusers (*Common › Companies*). **Where:** `/r/companies/`.

The companies of the group. Every business record belongs to one company; users work in the companies ticked on their profile and
switch between them in the top bar.

* **Fields:** name, abbreviation (shown in lists, e.g. the Users list), *is parent* / parent (group structure), legal name, tax id,
  address, factory address, e-mail, phone, fax, website, **logo** (shown in the top bar and on PDF letterheads), active.
* **Delete** is refused while other companies still have it as their parent.
* **Import:** yes (template on the import page).

### Departments
**Who:** staff & superusers (*Common › Departments*). **Where:** `/r/departments/`.

Departments shared by every company (no company field; names are unique), optionally nested (*parent*). Used on the user profile (*Department*), the Users list column
and reports. Fields: name, parent, active. Import: yes.

### Designations
**Who:** staff & superusers (*Common › Designations*). **Where:** `/r/designations/`.

Job titles shared by every company (name, active; names are unique). Shown on the user profile, under signatures on printed records and in observation
mails. Import: yes.

### Users
**Who:** staff & superusers. **Where:** `/r/users/`.

* **List:** photo, e-mail, full name, Department, Designation, Company (the default company's abbreviation), active. Department,
  Designation and Company are sortable. The list shows users of the active company.
* **Form tabs:**
  * **Profile** — e-mail, full name, active, official id, joining date, contact no., department, designation, photo, signature (the
    stored image is shown above the upload box; the signature is printed on approved records).
  * **Companies** — companies the user works in, and the default one.
  * **Roles & permissions** — roles and single permissions. Non-superusers can only hand out what they hold themselves.
  * **Object access** — limit the user to chosen records of a model (e.g. buyer *H&M* only). Nothing ticked for a scoped model = sees
    nothing of it. See [ACCESS_CONTROL.md](ACCESS_CONTROL.md).
  * **User access** — whose records the user sees on pages with an owner (own rows + the users ticked here).
  * **Approvals** — the approval steps this user approves ("Type › Step").
  * *Staff* / *Superuser* flags — superusers only.
* **Rules:** staff manage normal users only (not staff or superuser accounts); nobody deletes themselves; deleting is a soft delete
  and the account can no longer sign in. Import is off on purpose (it could hand out rights).

### Menu Builder
**Who:** superusers. **Where:** `/menu/`.

Edit the sidebar: add, rename, move (↑ ↓, change parent), hide (*active* off) or delete entries, up to `CORE_MENU_MAX_DEPTH` (6)
levels. Each link needs a **required permission** (except Dashboard) and may be marked **Superuser only**. Entries registered in code
(e.g. *Developer tools › Seed data*) and pinned saved searches are added automatically. `manage.py seed_default_menu` re-adds
missing defaults without touching your changes. Full guide: [MENU_BUILDER.md](MENU_BUILDER.md).

### Approval Types
**Who:** superusers (*Approval Builder › Approval Types*). **Where:** `/r/approval-types/`.

An approval type is the chain of steps a record goes through. Tabs:
* **Approval type** — name, **Applies to** (only models that can be approved), queue columns (record fields shown in *Awaiting my
  approval*), reference column (names the record in mails), reject mode (*Return to submitter* or *Back to previous approved step*),
  company (empty = all), type permission, who else is told when a request finishes (+ e-mail), active.
* **Steps** — the ordered steps; exactly one **final** (last). Per row: enabled, step permission scope, approver group, *only when*
  condition.
* **Approvers** — **May add steps** (people who may add an extra step to a pending request of *this* type, see
  [Approvals](#approvals)); after saving, the approver users of each step.
* **Print & export** — PDF / Excel buttons on the record page, and whether only after approval.

Details and examples: [APPROVALS.md](APPROVALS.md).

### Approval Steps
**Who:** superusers. **Where:** `/r/approval-steps/`.

Reusable steps used by the types: name, display name, signature label (heading on printed records, e.g. *Checked By*), pass / fail
labels and button text (what approvers see), default sequence, *offer Observation*, required permission, auto-skip rule, active.

### Approval Requests
**Who:** superusers. **Where:** `/r/approval-requests/`.

Read-only list of every request (record, type, status, submitter, date), filterable by status, type and date. The eye shows a
summary (steps in order, the waiting one in [brackets], added steps marked "(added)") and an **Open request page** link to the full
page with chain, history, decisions and *Add a step*, described under [Approvals](#approvals).

### Permissions
**Who:** staff & superusers (*Auth › Permissions*). **Where:** `/r/permissions/`.

Read-only list of every permission (they come from the code). Search by name or by `app.codename` (e.g. `core.add_user`), filter by
model, export. Use it to find the codename to put on a role, a menu entry or an approval step.

### Roles
**Who:** staff & superusers (*Auth › Roles*). **Where:** `/r/roles/`.

A role is a named set of permissions (a Django group). **Permissions** tab: tick permissions (grouped by app/model); **Members** tab:
the users who have it. Staff may add and change roles but only hand out permissions they hold; only superusers delete roles.
See [ACCESS_CONTROL.md](ACCESS_CONTROL.md).

### Notification Rules
**Who:** staff & superusers. **Where:** `/r/notification-rules/`.

"When X happens to a record, tell these people" — without code.
* **When:** record created / changed / deleted, sent for approval, approved, rejected. *Only when these change* (changed only) and
  *only for records matching* (a condition) narrow it.
* **Who:** chosen users and groups, the record's creator, the approval submitter, the approvers, the user in a field of the record,
  optionally the person who did it. Recipients are always limited to active members of the record's company who may view it.
* **What:** title and body with placeholders (`{record}`, `{model}`, `{event}`, `{actor}`, `{company}`, any field such as
  `{status}`), level, and channels: bell, internal mail, e-mail.

A broken rule is logged and skipped; it never blocks the save that triggered it. See [MAIL.md](MAIL.md) §3.

### Saved Reports
**Who:** superusers. **Where:** `/r/report-definitions/`.

Reports saved from the report builder (yours, and shared ones). Open one to run it again; delete what is no longer needed. They are
created on the [Reports](#reports) page, not here. See [REPORT_BUILDER.md](REPORT_BUILDER.md).

### Search builder
**Who:** superusers. **Where:** `/search/builder/`.

Build **Standard** searches for any list page or report: filters, columns and sort that everyone sees in the search drop-down,
optionally the page's default. See [SEARCH_BUILDER.md](SEARCH_BUILDER.md).

### Backups
**Who:** staff & superusers (restore: superusers). **Where:** `/backups/`.

* **Create database backup** — pick the mode: *Full* (restorable), *Schema only*, *Data only*, *Last 24 hours* (a partial extract,
  not a restore point). PostgreSQL uses `pg_dump` (custom format); SQLite a gzip SQL dump. Files go to `CORE_BACKUP_DIR` with a
  checksum sidecar.
* **Create file backup** — pick a source (Project, Static, Media, Private media, or `CORE_BACKUP_FILE_ROOTS`), extra exclusions,
  hidden files on/off (`.venv`, `venv`, `node_modules`, `__pycache__`, `.git` are always left out).
* **List:** date, mode, status (with the error if it failed), size, validation. Per backup: **Download**, **Validate** (re-checks the
  checksum and readability), **Restore…** (only when `CORE_BACKUP_ALLOW_RESTORE` is on; superusers; preflight + typed confirmation).
* Backups run in the background; you get a bell notice when one is done or failed. Old files are removed by
  `manage.py backup_retention` (`CORE_BACKUP_KEEP_DAYS`, always keeping `CORE_BACKUP_MIN_KEEP`).

Operations guide: [DEPLOYMENT.md](DEPLOYMENT.md) (Backups, Restore drill).

### Archive
**Who:** staff & superusers (restore: superusers). **Where:** `/archive/`.

Moves old rows of big tables out of the database into verified archive files (and back).
1. Pick a **table** (only those in `CORE_ARCHIVE_ALLOWED_TABLES`; system tables never) and **older than (days)** (at least
   `CORE_ARCHIVE_MIN_AGE_DAYS`).
2. **Preview** — how many rows match, how many are blocked because other records still point at them, how many will move.
3. Tick *I understand…* and **Run archive**. Rows are written to `CORE_ARCHIVE_DIR`, verified (count, checksum, keys) and only then
   deleted. A failed verification deletes nothing.
4. The list shows each job (moved / blocked rows, status). **Restore** puts an archived batch back.

### Export jobs
**Who:** staff & superusers. **Where:** `/exports/`.

Your own background exports (lists or reports bigger than `CORE_EXPORT_SYNC_MAX_ROWS`): target, format, status, requested time and
**Download** when done. Files expire after `CORE_EXPORT_EXPIRY_HOURS` (24 h). You get a bell notice when an export is ready.

### Background jobs
**Who:** staff & superusers in the menu (`core.view_jobrecord`: everybody's runs); any signed-in user opening `/jobs/` (e.g. from the
bell notice) sees the runs they started. **Where:** `/jobs/`.

Every run of a job a business app registered with `register_job` ([JOBS.md](JOBS.md)): job, status (Queued / Running n% / Done /
Failed with the error), who started it, requested time, how long it took, **Download** of its result file. Filter by job, status and
*Only mine*. A run's page shows parameters, result, runs and errors; **Run again** re-queues a failed run (your own, or anyone's with
`core.retry_jobrecord`). Result files older than `CORE_JOBS_KEEP_DAYS` (30) are deleted by retention; the rows stay.

### Audit log
**Who:** staff & superusers (superusers see every company). **Where:** `/r/audit/`.

Every create / change / delete / restore of audited records, sign-ins, approvals, exports and other events: when, event, user, model,
record, message. Open an event to see every field as **New** / **Old** lists, changed fields highlighted; passwords and other
sensitive values are redacted. Read-only; filter by event, date, model, user; export.

---

## Approvals
**Who:** users with `core.view_approvalrequest`. **Where:** `/approvals/`.

* **Awaiting my approval** — one tab per approval type, showing only requests *you* can decide now. Each row: the type's queue
  columns, created by / at, and the decision: pass / fail (and *Observation* where offered), a comment (required to reject or
  observe) and the step's button. The eye opens the record; the *Add a step* button (for people who may) opens the request at its
  add-step form.
* **My requests** (`/approvals/mine/`) — what you submitted, with status.
* **Request page** — the chain (current step marked, added steps marked *added*), the decision form, and the history.
* **Add a step** — on one pending request, for the people ticked under *May add steps* on its approval type (or holders of
  `core.add_step_to_request`, or superusers): step name, approver(s), and **Before** the waiting step (it goes to the new approver
  now, then back) or **After** it (it comes next). The approver(s) and the record's creator are mailed; if the added step is
  rejected, the record goes back to its creator.
* Records are sent for approval from their own page (**Submit**; **Withdraw** while pending).

Full guide: [APPROVALS.md](APPROVALS.md).

## Reports
**Who:** users with `core.view_reportdefinition`. **Where:** `/reports/`.

The list of report sources you may use, your saved reports and saved queries, and custom (code-defined) reports. Open a source in
the **report builder** (pick columns, filters, grouping, totals, chart; export or save), or start the **query builder** to join
related tables. You only ever see rows you could see on the list pages. See [REPORT_BUILDER.md](REPORT_BUILDER.md) and
[QUERY_BUILDER.md](QUERY_BUILDER.md).

## Mail
**Who:** users with `core.view_mailmessage`. **Where:** `/mail/`.

Internal mail between users of your companies: inbox, starred, sent, all mail, trash (and archive / restore per message); compose with attachments, reply / reply all /
forward; labels and filters (automatic sorting). System mails (approvals, notification rules, observations, added approval steps)
arrive here too. The top-bar envelope shows the unread count. See [MAIL.md](MAIL.md).

## Notifications
**Who:** users with `core.view_notification`. **Where:** `/notifications/`.

Your notices — the same ones as the top-bar bell: approval needed, approved / rejected, background jobs finished, notification
rules. Click one to open what it is about (it is marked read); filter by read / level / date. You only ever see your own.

---

## Developer tools › Seed data
**Who:** users with `seeder.use_seeder` (superusers have it). **Where:** `/seed/`. **Only when** the optional `core.seeder` app is
installed (dev / staging, never production); the entry is registered in code, not stored in the default menu.

Fills the database with realistic demo rows so every screen can be tried, and removes exactly those rows again.
* **Generate demo data** — the inputs, each explained on the page:
  * **Rows per model** (1–200, default 10): records made for every ticked model. Departments and designations get this many in
    all, shared out over the companies in turn. Inline rows (1–4 per record) come on top.
  * **New users** (0–50, default 5): demo people, password `demo-pass-12345`, role *Seed: business access*, access to all rows.
    `0` reuses the seeded users already there.
  * **New companies** (0–10, default 0): extra companies; existing ones are always used.
  * **Random seed**: any whole number (e.g. `42`). Empty = different data every run; the same seed on the same starting data
    replays the same values. Non-numbers are refused.
  * **Activity**: also submit / decide approval requests (raising notifications) and send a few internal mails.
  * **Model table**: tick the models to fill; *Rows* = rows in the table now, *Seeded* = rows the seeder made and still tracks.
* **Seeded batches** — one line per run (batch id, time, rows) with **Remove**, and **Remove all seeded data**. Removal deletes only
  seeded rows, newest first; rows that real data still uses are kept and reported.
* Writing is refused when `SEEDER_ENABLED` is off (default: follows `DEBUG`); the buttons are disabled and a warning shows.
* Same thing from the shell: `manage.py seed_data --count 20 --users 5 --seed 42`, `--list`, `--purge [batch]`.

Full guide: [SEEDER.md](SEEDER.md).

---

## Top bar
Always visible on desktop (on phones the sidebar header shows the same items in compact form). These are not menu rows, so the
Menu Builder does not change them.

| Item | What it does |
|---|---|
| **Fold button** (left) | Collapses the sidebar to icons (sub-menus open as fly-outs) or expands it. Remembered per browser. |
| **Company logo + name** | The active company's logo and name; click to go to the Dashboard. |
| **Theme** (sun / moon) | Switches light / dark. Saved on your profile and in the browser; until you choose, the device's theme is followed. |
| **Mail** (envelope) | Opens the [Mail](#mail) inbox; the red badge is the unread count (refreshes by itself). Shown with `core.view_mailmessage`. |
| **Bell** | Drop-down of your latest [notifications](#notifications) with the unread count; click one to open it, or go to the full list. Shown with `core.view_notification`. |
| **Account menu** (photo / name) | **Profile** (`/profile/`): your e-mail (read-only), full name, phone and the companies you belong to; *Save* stores name and phone (everything else is changed by an administrator on [Users](#users)). **Change password** (`/password/change/`): old password + new one twice; the password rules are shown on the form; you stay signed in. **Sign out** ends the session (an idle session is also signed out after `CORE_IDLE_TIMEOUT_SECONDS`, default 30 minutes). |
| **Company switcher** (right-most, only for users in more than one company) | Lists your companies; pick one to make it active. Every list, report, search and approval queue then shows that company's data. You stay on the current page when you may open it in the new company, otherwise you land on the Dashboard. |

Signed-out pages: **Sign in** (e-mail + password; repeated failures are throttled) and **Forgot password** (e-mails a reset link
when e-mail is configured).

