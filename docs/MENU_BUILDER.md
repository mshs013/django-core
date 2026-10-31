# Menu builder

The sidebar menu is **data**, not code. Every entry is a row in the `MenuItem` table. You edit it at
**Administration → Menu builder** (`/menu/`). The first install seeds a default menu; after that the rows belong to you.
Menus nest up to `CORE_MENU_MAX_DEPTH` levels (default 6, top level = 1); the editor refuses a parent that would go deeper
(the moved item's own children count too).

The page shows the menu as a tree: groups in bold with their item count, children indented under a branch mark, each entry's
icon. Columns: **Title**, **Target** (URL name or path, or *Group*), **Permission** (the code, *Staff* for `core.is_staff`, "—" for
none), **Superuser only** and **Active** (Yes / No; "group hidden" under an active entry whose parent is inactive). The filter box narrows the rows as you type; the
arrows move an entry up or down within its group (disabled at the ends).

The sidebar shows, in this order: the database rows (with the saved and custom reports placed in their groups, section 4.1), then
entries declared in code (section 7), then pinned saved searches.

## 1. The one rule to remember

**Menu visibility is presentation only.** The "permission" you set on a menu item decides *who sees the link*. It does **not**
protect the page. The target page always checks its own permission (see section 5). If you hide a link, the URL still works for
anyone who has the page's permission; if you show a link to someone without it, they get a 403 when they click.

So set the menu permission to the **same permission the target page needs**, so the link shows exactly for the people who can use it.

## 2. Menu item fields

| Field | Meaning |
|---|---|
| **Title** | Text shown in the sidebar (max 80 characters). Also the key used by the seeder to match default rows (title + parent). |
| **Icon** | Tabler icon name **without** the `ti-` prefix: `users`, `home`, `report`, `settings`. Browse names at tabler.io/icons. |
| **URL name** | A reversible Django URL name without arguments, such as `core:dashboard` or `core:users`. Preferred over Path. If it does not reverse, the item is silently hidden. |
| **Path** | An internal path starting with a single `/`, e.g. `/r/fabrics/`. Used only when URL name is empty. `//x`, `http://...` are rejected. |
| **Parent** | Another item. Empty = top level. Children appear under the parent as a dropdown; deeper levels open as indented sub-menus, up to `CORE_MENU_MAX_DEPTH` levels (default 6). |
| **Position** | Sort order inside the same parent (lower = higher up). Not unique. Use the **↑ / ↓** buttons to reorder; they renumber siblings 0, 1, 2 ... and swap. |
| **Required permission** | `app_label.codename`. **Required for every link except the Dashboard** (a group without a link may leave it empty). See section 3. |
| **Superuser only** | Only superusers see the item (and its children). Overrides everything else. |
| **Active** | Untick (in Edit) to hide without deleting. A hidden group hides its children. |

A **group** is an item with no URL name and no path. It may be created empty (the list shows *Group (empty)*): the sidebar hides it
until something visible is under it, a menu item or a saved report placed there (section 4.1).

### Linking a CRUD list page

The generic list is `/r/<slug>/` (URL name `core:resource_list` with the slug as an argument). A URL name cannot carry an argument, so use
**Path**: `/r/fabrics/`. `core`'s own resources (users, companies, departments ...) already have argument-free names such as `core:users`
and `core:companies`; use those for them.

## 3. Which permission can I use? (the "Required permission" field)

The Menu builder checks the field on save:

* **Every link needs a permission.** The only exception is a link to the Dashboard (URL name `core:dashboard`, or Path `/`), the one page
  open to every signed-in user. A group with no link of its own may leave it empty; it shows when one of its children is visible.
* **The permission must exist.** A typo is refused ("No permission …"), so an entry can no longer be hidden by a misspelt codename.
  `core.is_staff` (section 3.5) is accepted too.

Entries declared in code (section 7) are not checked this way; give them a permission yourself.

### 3.1 The everyday pages
These pages check the same permission on the page itself, not only in the menu (without it the page answers 403, and Mail / the bell are
hidden from the top bar):

| Page | Permission |
|---|---|
| Dashboard | none (everyone signed in) |
| Approvals (queue and *My requests*) | `core.view_approvalrequest` |
| Reports | `core.view_reportdefinition` |
| Mail (all mail pages, top-bar icon) | `core.view_mailmessage` |
| Notifications (list, bell, dropdown) | `core.view_notification` |

Give them to users through a role. On upgrade (migration `0025`) they were granted to every existing role and to every user without a role,
so nobody lost access; **new users get them only through their roles.** Named approvers need `core.view_approvalrequest` to open their queue
(they can still open a request from its notification).

Granting `core.view_approvalrequest` or `core.view_reportdefinition` also opens the list pages *Approval requests* and *Saved reports*
(`/r/approval-requests/`, `/r/report-definitions/`) to that user, scoped to what they may see; their menu entries stay superuser-only.

### 3.2 Model permissions: `view`, `add`, `change`, `delete`
Django creates four for every model: `<app>.view_<model>`, `<app>.add_<model>`, `<app>.change_<model>`, `<app>.delete_<model>`.
Use the **view** one for a list page.

| Menu entry | Permission |
|---|---|
| Users | `core.view_user` |
| Companies | `core.view_company` |
| Menu builder | `core.view_menuitem` |
| A business list, e.g. Garments in app `garments` | `garments.view_garment` |

The CRUD list page itself needs this same `view` permission (`ResourceConfig.perm("view")`), so use it for the menu item too.

### 3.3 Custom model permissions
Models can declare extra permissions in `Meta.permissions`. Examples already in `core`:

| Permission | Meaning |
|---|---|
| `core.add_step_to_request` | inject an extra step into a pending approval request |
| `core.build_search` | build and save searches (Search builder) |
| `core.run_archive` / `core.restore_archive` | run / restore a data archive |

Your own apps work the same way, e.g. `garments.approve_garment` in an approval chain, or `seeder.use_seeder` (the demo-data page uses it).
List all codenames with:

```bash
python manage.py shell -c "from django.contrib.auth.models import Permission as P; [print(f'{p.content_type.app_label}.{p.codename}') for p in P.objects.order_by('content_type__app_label','codename')]"
```

or look them up on **Administration → Permissions**.

### 3.4 URL access permissions: `core.access_<namespace>_<url_name>`
`python manage.py seed_url_permissions` creates one permission per named URL, e.g. `knitting:yarn_stock` becomes
`core.access_knitting_yarn_stock`. `UrlPermissionMiddleware` requires it **only for views that do not check permissions themselves**,
typically plain views in your business apps. All of `core`'s own pages (CRUD lists, backups, exports, reports, builders) check their own
permissions and ignore these, so `core.access_core_…` codenames do not control them. Use an `access_` permission as the menu permission
for a business page protected this way. Run `seed_url_permissions --dry-run` to list what would be created.

### 3.5 The staff pseudo-permission: `core.is_staff`
Not a database row. It is true for superusers and for users with the **Staff** flag. The default menu uses it for **Administration**,
**Audit log**, **Roles**, **Permissions** and the **Common** group. Staff also implicitly hold (no permission row needed):
`core.view_user` / `add_user` / `change_user`; view / add / change / delete of departments, designations and notification rules;
`core.view_backuprecord`, `create_backup`, `download_backup`, `validate_backup`; `core.view_archivejob`, `run_archive`;
`core.view_exportjob` (`STAFF_IMPLIED` in `core/permissions/service.py`). Restoring backups or archives and deleting backups stay
with superusers (or whoever is given those permissions).

### 3.6 Superuser only (the tick box)
Not a permission string. Use it for pages only a superuser should ever see (Companies, Backups, Approval types ...). It hides the item for everyone
else regardless of grants.

### What does NOT work
* A role or group name (`Managers`): the field wants a permission codename, not a role. Grant the permission to the role instead.
* Several permissions in one field (`a.view_x, a.view_y`): only one is checked. For "either of two", leave it empty and rely on the
  page, or give both groups of users the same single permission.
* Object-level or company-specific permission: the menu check is global (no object, no company). A user granted a permission only for one
  company's objects will not see the link unless they also hold it globally.

## 4. Typical recipes

**Add a top-level "Garments" group with two pages**

1. New item: Title `Garments`, Icon `shirt`, Permission empty, Parent empty, no URL name and no Path (a group).
2. New item: Title `Garments`, Path `/r/garments/`, Permission `garments.view_garment`, Parent `Garments`.
3. New item: Title `Fabrics`, Path `/r/fabrics/`, Permission `garments.view_fabric`, Parent `Garments`.

Users with neither permission do not see the group at all: a group with no visible children and no URL of its own is dropped automatically.

**Show a page only to managers:** give the role `view_<model>` and set the same permission on the item.

**Hide a page for now:** Edit, untick Active. Children of a hidden group are hidden with it.

**Reorder:** use ↑ / ↓. Moving only changes order among siblings (same parent).

### 4.1 Reports in the menu: Inventory › Yarn / Yarn dyeing

Saved reports (report builder and query builder) and custom reports can sit in the menu like pages. Example:

```
Inventory
├─ Yarn          → Daily receive · Daily issue · Stock report · Stock ledger
└─ Yarn dyeing   → Daily receive · Daily issue · Stock report · Stock ledger
```

1. **Menu builder** (once): New item `Inventory` (icon `box`, no URL name, no Path, Permission empty), then `Yarn` and `Yarn dyeing`
   with Parent `Inventory`. Empty groups are allowed; they stay hidden until something visible is placed in them.
2. **Each builder report**: open it in the report builder (or query builder), and on the Save form pick **Show in menu under** →
   `Inventory > Yarn`, optionally a **menu label** (default: the report name, e.g. `Daily receive` while the report is called
   *Yarn daily receive*) and a **position** (lower = higher up; e.g. 10 / 20 / 30). Save. Choose who may open it as usual
   (Only me / Selected users / groups / All users / Standard): the menu shows it to exactly those people.
3. **Stock ledger** (a custom report, see REPORT_BUILDER.md §6.7): in the business app,
   `register_stock_ledger("yarn-ledger", "Yarn stock ledger", YarnMove, ..., menu="Inventory > Yarn", menu_label="Stock ledger",
   menu_position=40)`. The `menu` text is the group's titles from the top level, matched case-insensitively.

| Who sees a report entry | Rule |
|---|---|
| Saved report / query | People who may open it (owner; *All users*; the users and groups it is shared with; *Standard* = everyone) **and** who hold the source's permission (query builder: the root table's). A private report shows only to its owner; its name never reaches anyone else. |
| Custom report | Holders of the report's `permission`. |
| All of them | Only under an active group (and active groups above it) that the user can see (superuser only / permission). A group with nothing visible in it is dropped. |

Reports and menu items in the same group are ordered together by position (items: *Position*; reports: *menu position*), then by
title. Deleting the report, or clearing *Show in menu under*, removes the entry; deleting the group only clears the reports'
placement (they are not deleted). The picker on the Save form lists only active groups the user can see, with room for one more level
(`CORE_MENU_MAX_DEPTH`), and is shown only to people who may change the report; the server checks the choice again (an unknown,
inactive or hidden group, or an item with a URL name / Path, is refused). Clones and "Save as my copy" start out of the menu. A report
in the menu has a ☰ icon on the Reports page. A link to a saved report is highlighted in the sidebar while that report is open.

## 5. Where the real security is

| Layer | What it does |
|---|---|
| Menu item permission | Hides or shows the link. Convenience. |
| `UrlPermissionMiddleware` | Every named URL needs `core.access_<ns>_<name>` unless the view declares its own checks (`core_self_authorizing`). Unnamed URLs are refused for non-superusers. |
| The view itself | CRUD, approvals, mail and the builders check `view/add/change/delete` and company scope on every request. |
| Company scope | Out-of-scope records return 404 (they do not leak existence). |

Because of this, a wrong menu permission can never expose data. It can only show a link that gives 403, or hide a link that would work.

## 6. Permissions needed to *use the Menu builder itself*

| Action | Permission |
|---|---|
| Open the list | `core.view_menuitem` |
| New item | `core.add_menuitem` |
| Edit (incl. Active), reorder (↑ ↓) | `core.change_menuitem` |
| Delete (children are deleted with it) | `core.delete_menuitem` |

The default menu entry for the Menu builder is **superuser only**. To let an administrator role manage the menu, grant the role the four
permissions above and untick "Superuser only" on that menu item.

## 7. Menu entries from code (business apps)

A business app can add entries without touching the database, from `AppConfig.ready()`:

```python
from core.menu.service import register_menu_entry

register_menu_entry("Garments", path="/r/garments/", icon="shirt",
                    permission="garments.view_garment", parent="Production", position=20)
```

`parent` is the **title** of a top-level group (created on demand). Code entries are added after the database entries and use the same permission
rule. They cannot be edited or reordered in the Menu builder. For a menu you want to curate, prefer database rows.

Optional: `CORE_MENU_AUTO_RESOURCES = True` adds an automatic **Data** group listing every registered CRUD resource the user may `view`.

Saved searches **pinned to the menu** appear in an automatic **Saved searches** group: the user's own, those shared with the active
company, and standard (system) searches, at most 20, each only if the user may `view` that list. See `docs/SEARCH_BUILDER.md`.

## 8. Defaults, caching and troubleshooting

* The default tree is `DEFAULT_MENU` in `core/menu/defaults.py`. Every link has a permission except Dashboard:

  | Entry | Permission | Superuser only |
  |---|---|---|
  | Dashboard | none | |
  | Approvals / Reports / Mail / Notifications | see section 3.1 | |
  | Administration (group) | `core.is_staff` | |
  | › Common (group) | `core.is_staff` | |
  | › › Companies | `core.view_company` | yes |
  | › › Departments, Designations | `core.view_department`, `core.view_designation` | |
  | › Users | `core.view_user` | |
  | › Menu Builder | `core.view_menuitem` | yes |
  | › Approval Builder › Types, Steps, Requests | `core.view_approvaltype`, `core.view_approvalstep`, `core.view_approvalrequest` | yes |
  | › Auth › Permissions, Roles | `core.is_staff` | |
  | › Notification Rules | `core.view_notificationrule` | |
  | › Saved Reports, Search builder | `core.view_reportdefinition`, `core.build_search` | yes |
  | › Backups, Archive, Export jobs | `core.view_backuprecord`, `core.view_archivejob`, `core.view_exportjob` | |
  | › Background jobs | `core.view_jobrecord` | |
  | › Audit log | `core.is_staff` | |

  Rows without the tick are seen by staff and superusers (staff hold those permissions implicitly, section 3.5). Every page is
  described in [MENUS.md](MENUS.md).

  The superuser-only rows carry a permission so you can delegate one later: untick **Superuser only** and grant the permission to a role.
  (Search builder pages themselves stay superuser-only in code.)
* `python manage.py seed_default_menu` (also run by `bootstrap`) adds the missing default rows. It never edits or deletes yours (rows are matched
  by title + parent), so a deleted default comes back if you run it again, and a **renamed** default is added again beside your renamed copy.
  Run it after a restore into an empty database.
* The menu is cached per user and company for `CORE_MENU_CACHE_SECONDS` (default 300). Every save, delete, reorder or toggle in the Menu builder clears it
  immediately. Permission grants clear it too, and so does any change to a saved report that is (or was) in the menu: its group, label,
  position, name, sharing, or deleting it.
* Cycles are prevented: an item cannot be its own ancestor.

| Symptom | Check |
|---|---|
| Item missing for a user | Is it Active? Superuser only? Does the user hold exactly that permission (spelling `app.codename`)? Is the parent visible? |
| Item shows but page gives 403 | The page needs a different permission than the menu item. Set both the same. |
| Group disappeared | All its children are hidden for that user (or inactive), and the group has no URL. |
| Report not in the menu | The user may not open it (private / not shared), lacks the source's permission, or its group (or one above) is inactive or hidden for them. A custom report's `menu=` text must match the group titles exactly (case aside). |
| Link missing but the row exists | The URL name does not reverse (typo, or the URL needs an argument). Use Path instead. |
| Change not visible | Cached up to 5 minutes after a grant made outside the app (for example in SQL); saves in the app clear it at once. |

## 9. Settings and code

| Setting | Default | Meaning |
|---|---|---|
| `CORE_MENU_CACHE_SECONDS` | `300` | How long a user's built menu is cached |
| `CORE_MENU_AUTO_RESOURCES` | `False` | Add the automatic **Data** group (section 7) |

Both are set in `config/settings/base.py` (they are not read from `.env`).

| File | What |
|---|---|
| `core/models/menu.py` | `MenuItem` (internal paths only, no cycles) |
| `core/menu/service.py` | `menu_for(user, company_id)` builds the tree; `register_menu_entry`; `invalidate_menus` |
| `core/menu/defaults.py` | `DEFAULT_MENU` and `seed()` |
| `core/views/menu.py`, `core/templates/core/menu/` | The Menu builder pages |
| `core/management/commands/seed_default_menu.py` | Adds missing default rows |
| `core/templates/core/base.html` | Renders the sidebar (two levels) |
