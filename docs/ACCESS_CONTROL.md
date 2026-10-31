# Access control: roles, permissions, object access, user access

Who may see and do what, from the broadest rule to the narrowest. Each layer narrows the one before it, and **superusers pass
every layer**.

| Layer | Question it answers | Set up in |
| --- | --- | --- |
| Company | Which company's data? (the active company) | User form › **Companies** |
| Model permission | May this user view / add / change / delete *orders* at all? | **Roles** and single **permissions** (User form › Roles & permissions) |
| Object access | *Which* orders? Only those of buyer H&M | User form › **Object access** |
| User access | Whose orders? Their own plus those of Ann and Bob | User form › **User access** |
| Object permission (guardian) | This one record only | code: `core.permissions.assign_perm` |

All layers are applied in the same place for every read: list pages, details, edit forms (including the choices offered in
dropdowns), exports, search, the report builder and the query builder.

---

## 1. Roles and permissions (Administration › Roles, Administration › Permissions)

* **Roles** are Django auth groups under the name users see. A role is a named set of permissions; give it to users on its
  **Members** tab or on the user's **Roles & permissions** tab.
* **Permissions** lists every permission in the system (read-only: they come from the code), searchable by name and filterable by
  model.
* **Staff** (users with *is staff*) see the Administration menu with Users, Common › Departments / Designations, Auth › Roles /
  Permissions, Notification Rules, Backups, Archive, Export jobs and Audit log (the full list per menu is in [MENUS.md](MENUS.md)).
  They may **add and change** roles; only **superusers delete** them. Companies, Menu Builder, Approval Builder, Saved Reports and
  Search builder stay superuser-only.
* **No privilege escalation:** an editor who is not a superuser can only hand out what they hold themselves:
  * the permission picker lists only permissions the editor has;
  * the role picker on the user form lists only roles made entirely of such permissions;
  * a role holding permissions the editor lacks keeps its members locked for that editor.

  Anything the editor cannot see (permissions, roles, members) is kept unchanged when they save.

* **Everyday pages:** Approvals, Reports, Mail and Notifications need `core.view_approvalrequest`, `core.view_reportdefinition`,
  `core.view_mailmessage` and `core.view_notification` (page and menu alike); only the Dashboard is open to everyone. Put these four in
  the role(s) every user gets. See `docs/MENU_BUILDER.md` section 3.1.

The permission picker groups permissions by *app › model*. Tick a heading to tick the whole group, and type in the filter box
(e.g. `garment`, `approve`) to narrow the list.

---

## 2. The user form

The form is split into tabs:

| Tab | Contents |
| --- | --- |
| **Profile** | E-mail, name, password (new users), department, designation, ids, photo, signature, active. |
| **Companies** | Default company and the companies the user works in. |
| **Roles & permissions** | Roles and/or single permissions, either or both. |
| **Object access** | For each object-access source (e.g. *Buyers*), the rows this user works with, or **All**. |
| **User access** | The users whose records this user also sees on owner-scoped pages, or **Everyone's records**. |
| **Approvals** | The approval steps this user may decide, and the approval types on whose pending requests they may add a step (shown to superusers and users who may change approval types). |
| **Reports** | *Report shows all companies*; when ticked, *Report companies* (Active company / All my companies / Chosen companies) and, for *Chosen*, the companies. The select and the list appear only when they apply; the server checks them anyway: chosen companies must be among the user's companies (any company for a superuser) and at least one must be ticked. Off = reports and exports use the active company only. See REPORT_BUILDER.md 2.3. |

A tab with an error shows a red dot, and the first such tab opens.

Every change made here is audited on the user (docs/AUDIT.md): companies, roles, single permissions, object access, user access,
approver steps and "may add steps" each give one readable row (old and new names) when they change, and no row when they do not.
Role permission changes are logged on the role; object permissions (`assign_perm` / `remove_perm`) on the object.

---

## 3. Object access ("this user works only for buyer H&M")

A developer declares which model is the *source* of access and which models follow it:

```python
# myapp/registrations.py (imported from AppConfig.ready)
from core.permissions.access import register_access_scope

register_access_scope(Buyer)                                     # the buyer list itself
register_access_scope(Buyer, target=Order, path="buyer")         # orders follow their buyer
register_access_scope(Buyer, target=OrderLine, path="order__buyer")
```

Then, on the user form's **Object access** tab, tick the buyers the user works with:

* The **Buyers** list shows only those buyers. **Orders** (and order lines) show only orders for those buyers, in lists, details,
  exports, reports and the query builder (joined rows included).
* The **Buyer** dropdown on the order form offers only those buyers, so the user cannot create an order for another buyer.
  Posting another buyer's id is refused.
* **No buyer ticked = no buyers and no orders** for that user. Tick **All buyers (no limit)** to lift the limit.
* An editor who is limited themselves can only assign rows they may see, and never "All".

Assignments are `core.models.ObjectAccess` rows: `(user, content_type, object_id)`, where an empty `object_id` means "all".

## 4. User access ("this user also sees Ann's and Bob's records")

Register the resource with the field that holds the record's owner:

```python
register_resource(Order, ..., owner_field="created_by")
```

On that page every user sees **their own records plus those of the users on their User access tab**. **Everyone's records** lifts
the limit. The model permission is still required. Pages without `owner_field` are not affected. Rows are
`core.models.UserAccess`: `(user, target)`, where an empty `target` means "everyone".

## 5. In your own code

```python
from core.permissions.access import restrict, access_q, allowed_ids, visible_owner_ids

qs = restrict(Order.objects.all(), request.user)        # object access + user access
q = access_q(Order, request.user, prefix="order")       # the same condition reached through a relation (or None)
allowed_ids(user, Buyer)                                # list of ids, or None = all
visible_owner_ids(user)                                 # list of user ids (always incl. the user), or None = everyone
```

`core.crud.access.scoped_queryset` (used by every CRUD view, export and search) already applies `restrict`. So do the report
builder's `scoped_base` and the query builder's root and join scope. Custom reports that build their own querysets should call
`restrict` too.

## 6. Form options that came with this (any resource)

```python
register_resource(Order, ...,
                  tabs=(("Order", ("ref", "buyer", "qty")), ("Lines", ("@lines",))),    # "@slug" = an inline
                  m2m_widgets={"tags": "checkbox"})                                      # or "select" (default)
```

* `tabs`: the form in tabs. Fields not listed go on the first tab. A custom form class can set `self.tabs` instead.
* `m2m_widgets`: many-to-many fields as a filterable checkbox list instead of a multi-select. The site-wide default is the setting
  `CORE_M2M_WIDGET = "select" | "checkbox"`. `Inline(..., m2m_widgets=...)` works the same way.

## 7. File map

| Path | What |
| --- | --- |
| `core/permissions/access.py` | `register_access_scope`, `restrict`, `access_q`, caches |
| `core/permissions/grants.py` | what an editor may hand out (`grantable_permissions`, `grantable_roles`) |
| `core/models/access.py` | `ObjectAccess`, `UserAccess` |
| `core/models/identity.py` | `Role` (proxy of `auth.Group`) |
| `core/crud/userform.py` | the tabbed user form |
| `core/crud/roleform.py` | the role form (permissions, members) |
| `core/crud/widgets.py` | `CheckboxList`, `GroupedCheckboxList`, `permission_choices_field` |
| `core/crud/forms.py` | `build_tabs`, `m2m_widgets_for`, `scope_form` (choices limited by access) |
| `tests/test_access.py`, `tests/test_roles.py` | behaviour tests |
