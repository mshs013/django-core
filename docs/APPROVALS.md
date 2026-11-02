# Dynamic Approval System

Multi-step approval chains for any record: submit → step 1 → step 2 → … → final approval, with per-step approvers, conditional and
auto-skipped steps, rejection that returns to the submitter or to the previous step, resubmission rounds, extra steps added on the fly,
record locking, notifications, an append-only history and signature boxes for printed documents.

Chains can be **configured entirely in the app** (no code) or **defined in code** and then managed in the app.

Code: `core/approvals/` · models: `core/models/approvals.py` · views: `core/views/approvals.py` · templates:
`core/templates/core/approvals/` · tests: `tests/test_approvals.py`

---

## 1. Concepts

| Thing | Model | What it is |
|---|---|---|
| **Step** | `ApprovalStep` | A reusable step: `code`, `name`, default required `permission`, optional auto-skip rule name. |
| **Approval type** (chain) | `ApprovalType` | An ordered list of steps for one kind of record: `code`, `name`, *Applies to* (model), company (blank = all), optional type permission, reject mode, who else to notify. |
| **Step in a chain** | `ApprovalTypeStep` | A step placed in a type: `position`, `is_final` (exactly one), `is_enabled`, permission override, **approver users** (any number) and/or an **approver group**, and an *only when* condition. |
| **Request** | `ApprovalRequest` | One record going through a chain: status, round, current step, and a **frozen snapshot of the chain** taken at submission. |
| **History** | `Approval` | Append-only log: submitted, approved, rejected, skipped, cancelled, resubmitted, step added — with actor, step, comment, time. |
| **Approvable record** | your model + `AuditedModel` | Gets `is_confirm` (sent), `is_approval` (approved), `approved_at`, set only by the engine. |
| **Cancelable record** | your model + `CancelableModel` | Adds `is_cancel`, `canceled_by`, `canceled_at` and a Cancel action (also after approval). |

**Statuses:** `none` (never sent) → `pending` → `approved` · `rejected` · `cancelled`. Only one pending request per record is allowed
(database constraint).

**Why a frozen snapshot?** Editing a chain in the app never changes requests already in flight: each request keeps the steps it was
submitted with. New submissions use the new chain.

---

## 2. Using it (end users)

### 2.1 Submitting
Open a record. Records with an approval show the status badge and the approval buttons: in the record's pop-up they sit at the
top right of the header, just before the close button; on the full detail page they are in an **Approval** card.
* **Submit for approval** — sends it; the first step's approvers get a notification. The record is now *Pending* and (by default)
  locked for editing.
* **Resubmit** — after a rejection: starts a new *round* through the chain.
* **Withdraw request** (pending, non-cancelable records) / **Cancel** (records with `CancelableModel`, also after approval).
* **View request** — the request page with the chain and history.

List pages of approvable models show an approval-status column.

### 2.2 Deciding
* **Approvals** (`/approvals/`) — the queue of requests *you* can decide right now (*Review* opens one).
* On the request page: **Approve** (comment optional) or **Reject** (comment **required**). If someone else decided the step while you
  were looking, you get "This request changed while you were looking at it. Reload and try again." instead of a double decision.
* **Observation** (only on steps with *offer Observation* ticked; comment **required**): approve *with a remark*. The record's creator
  gets the observation by internal mail + bell (and e-mail when e-mail notices are on), and the request **moves on to the next step**
  exactly like an approval (on the final step it finishes as approved). The step counts as signed on the signature row.
* **Add a step** to *one* pending request (card at the bottom of the request page; the queue shows a
  *Add a step* button next to the eye for people who may do it). Who may: the people ticked under **May add steps** on the
  request's approval type (Approvers tab) — only for that type's requests —, holders of `core.add_step_to_request` (every type), and
  superusers; they must also be able to see the record. Fill in:
  * **Step name** (the code is generated), **Approver** (one or more active members of the record's company; never the submitter
    unless self-approval is on) and an optional **Comment** (copied into the mails);
  * **Where**: **Before** the waiting step — the request goes to the new approver now and, once they approve, comes back to the step
    that was waiting; or **After** the waiting step — the waiting step is decided first, then the new step, then the rest of the
    chain. Never after the final step (only *Before* is offered then).

  The new step's approvers and the record's **creator** (else the submitter) get an internal mail (and an e-mail when e-mail notices
  are on); with *Before*, the approvers also get the usual "Approval needed" bell. If the added step's approver **rejects**, the
  request is rejected and the record goes back to its creator (who is mailed) — whatever the type's reject mode. Added steps are
  marked *added* in the chain and logged in the history.
* **My requests** (`/approvals/mine/`) — everything you submitted, with status.

### 2.3 Who may decide a step
All of these must hold:
1. The request is pending, and the user is active and a member of the record's company.
2. The user is not the submitter — unless `CORE_SELF_APPROVAL = True` (off by default, superusers included).
3. If the approval type has a company, it is the record's company; if it has a *type permission*, the user holds it.
4. The user can **view** the record (object-level permission) — not needed when they are named on the step (approver user or
   approver group): picking someone by name is enough.
5. And the step allows them: they are one of the step's **approver users**, or in its **approver group**, or hold the step's **permission**
   (chain-level override first, else the step's own). Superusers always pass this last check. A step with none of these can only be
   decided by superusers.

The same rule (`_may_decide`) decides who is *told* to approve, so whoever gets an "Approval needed" notice always finds the
request under **Awaiting my approval**, and nobody is told who cannot act.

### 2.4 Who may submit / withdraw / cancel
* **Submit / resubmit / withdraw:** the target's `submit_permission` if set (code targets), otherwise anyone with *add* or *change*
  permission on the record; member of its company.
* **Cancel** (`CancelableModel`): *change* permission on the record.

---

## 3. Configuring chains in the app (administrators, no code)

1. **Approval steps** (`/r/approval-steps/`) — create reusable steps, e.g. `check` "Checked By" (permission
   `purchase.check_yarnworkorder`), `audit` "Audited By", `approve` "Approved By". **Signature label** is the heading of the step's
   box on the signature row of a record (e.g. "Checked By"; empty = the step name).
2. **Approval types** (`/r/approval-types/`) — create a type:
   * **Code / Name** — e.g. `yarn-wo` / "Yarn work order approval".
   * **Applies to** — pick the model (any model with a list page). Every record of it now gets the Approval card. *No code needed.*
   * **Company** — blank = every company; or one company (that company's type wins over a blank one).
   * **Type permission** — optional extra permission needed to see/act on any request of this type.
   * **Reject mode** — *Return to submitter* (request ends as rejected) or *Back to previous approved step*.
   * **Tell others / users / groups / also e-mail** — who else is notified when a request is finally approved/rejected (the submitter
     always is).
   * **Steps** (inline rows) — **+ Add step** for each step, in order (↑ ↓ to reorder; the order shown is saved), tick **final** on
     exactly one (it must be last), and optionally:
     **enabled** (disable instead of deleting — disabled steps are skipped and logged), **step permission scope** (overrides the step's
     permission in this chain), **approver group**, **only when** condition.
   * **Approvers** tab: **May add steps** — the people who may add an extra step to a pending request of this type (see 2.2).
     After the type is saved, for each step, tick any number of **approver users**. The same assignment can be
     made from the other side, on a user's form, **Approvals** tab ("Type › Step"; the same tab also has *May add steps on these
     approval types*). Requests submitted before a change keep the
     approvers they were submitted with.
   * **Print & export** tab: **Export buttons** (*PDF and Excel* / *PDF only* / *Excel only* / *None*) and **Show export buttons**
     (*All the time* / *Only when approved*) for the record's detail page. *Page default* keeps what the page registers in code
     (`register_resource(detail_exports=..., detail_export_when=...)`, default: PDF and Excel, all the time). "Only when approved"
     hides the buttons and refuses the download until the request is finally approved.
3. Reorder with ↑ ↓; the next submission uses the new order. If a step row has a problem, the form shows "Please correct the errors
   below" at the top and the message on the row.

### "Only when" conditions
A step can run only for matching records; otherwise it is skipped and the skip is logged. The condition uses the list page's
filterable fields and the search-builder format ([SEARCH_BUILDER.md](SEARCH_BUILDER.md) §3):
```json
{"field": "total", "op": "gte", "value": "10000"}
[{"field": "total", "op": "gte", "value": "10000"}, {"field": "currency", "op": "eq", "value": "USD"}]
{"op": "OR", "items": [{"field": "total", "op": "gte", "value": "10000"}, {"field": "source", "op": "eq", "value": "import"}]}
```
The final step cannot have a condition (it always runs).

### Validation rules (enforced on save and at submission)
Exactly one enabled final step, and it is last; the final step cannot be disabled, auto-skipped or conditional; no step twice in a
chain; unknown skip-rule names are rejected at submission rather than mid-flow.

---

## 4. How a request flows

```
submit ─► first runnable step ─► approve ─► next runnable step ─► … ─► approve FINAL ─► APPROVED (is_approval=True, on_approved)
                     │                                  │
                     └── reject ─► return_to_submitter: REJECTED (is_confirm=False, on_rejected) ─► resubmit (round + 1)
                                   previous_step:       back to the last step approved in this round (if none: REJECTED)
```
* **Runnable step** = enabled, not auto-skipped by its skip rule, and matching its condition. Every passed-over step is written to the
  history as *Skipped* with the reason.
* **Locking:** while the status is in the target's `lock_statuses` (default *pending* and *approved*) the record cannot be edited.
* **Concurrency:** decisions lock the request and the record row (`select_for_update`); forms carry `expected_sequence` so a stale
  page cannot decide a step twice.
* **Notifications:** everyone who may decide the next step (superusers only when named, or as a fallback when nobody else
  qualifies; never the submitter unless self-approval is on). The notice links to the request. When nobody at all can decide the
  step, the submitter gets a "No approver" warning instead. The submitter is also told
  on the outcome, plus the type's *tell others* list. Sent after the transaction commits.
* **Audit:** every history row is also an `approval` audit event.

---

## 5. Extending it (developers)

### 5.1 Define a chain and a target in code
```python
# myapp/apps.py -> ready(): from . import registrations
from core.approvals import StepSpec, register_chain, register_skip_rule, register_target
from .models import YarnWorkOrder        # class YarnWorkOrder(CompanyScopedSoftDeleteModel, AuditedModel, CancelableModel)

register_chain("yarn-wo", "Yarn work order approval", [
    StepSpec("check", "Checked By", permission="purchase.check_yarnworkorder"),
    StepSpec("audit", "Audited By", permission="purchase.audit_yarnworkorder", skip_rule="small_order"),
    StepSpec("approve", "Approved By", permission="purchase.approve_yarnworkorder"),        # last = final
], reject_mode="previous_step")

register_skip_rule("small_order", lambda obj, request: obj.total < 1000)   # True = skip (logged)

register_target(
    "yarn-wo", YarnWorkOrder,                       # the model must inherit core.models.AuditedModel
    chain="yarn-wo",                                # default: same as the target key
    title_builder=lambda wo: f"Yarn WO {wo.number}",
    url_builder=None,                               # default: the record's detail page
    submit_permission="purchase.submit_yarnworkorder",   # default: add/change permission on the record
    on_approved=lambda wo, req: wo.release(),       # runs inside the deciding transaction
    on_rejected=None,
    lock_statuses=("pending", "approved"),          # any subset of these two
)
```
and on the model: `approval_target = "yarn-wo"`.

* `register_chain(code, name, steps, reject_mode=, permission=)` — steps are `StepSpec(code, name, permission="", skip_rule="",
  enabled=True)` or dicts. The last step is final and cannot be disabled or auto-skipped.
* On first submission (or `python manage.py sync_approval_chains`) the chain is copied into `ApprovalType` / `ApprovalStep` /
  `ApprovalTypeStep` rows. From then on **the database is the truth**: administrators can reorder, re-assign or disable steps in the
  app, and syncing never edits, reorders or deletes existing rows.
* A code target wins over an app-configured type for the same model.
* Skip rules: `register_skip_rule(name, func)` where `func(obj, approval_request) -> bool`. They are named in steps (code or app).

### 5.2 Service API (`from core import approvals`)
Every function is atomic, authorises through `core.permissions` (company- and object-aware, fail closed) and raises
`approvals.ApprovalError` with a user-friendly message.

| Function | Purpose |
|---|---|
| `submit_for_approval(user, obj, extra_steps=None, comment="")` | Create a request (or resubmit a rejected one). `extra_steps=[{"code", "name", "permission", "approver_user_ids": [...], "approver_group_id", "after": "<step code>"}]` adds per-record steps (a single `approver_user_id` is still accepted). |
| `submit_approval_step(user, request_id, "approve"/"reject", comment="", expected_sequence=None)` | Decide the current step. |
| `resubmit(user, obj, comment="", extra_steps=None)` | New round after rejection. |
| `cancel(user, obj, comment="")` | Cancel a `CancelableModel` row, or withdraw a pending request otherwise. |
| `add_step_to_request(user, request_id, step, position="after", after_sequence=None, expected_sequence=None, comment="")` | Add an extra step to one pending request: `step = {"name": ..., "approver_user_ids": [...]}` (optional `code`, `permission`); `position` `"before"` / `"after"` the waiting step. Mails the approvers and the creator. |
| `can_add_step(user, req)` / `step_approver_choices(req)` | May the user add a step here? / Who may be named its approver. |
| `can_submit(user, obj)`, `can_act(user, req)`, `can_view(user, req)`, `can_cancel(user, obj)` | Permission checks for your own UI. |
| `status_for(obj)`, `statuses_for(objs)`, `is_locked(obj)`, `has_approval(obj)` | State (the plural form costs one query per page). |
| `get_pending_approvals(user)`, `pending_count(user)`, `my_requests(user)` | Queues. |

Example — a custom "Approve all" button for a manager:
```python
for req in approvals.get_pending_approvals(request.user):
    try:
        approvals.submit_approval_step(request.user, req.pk, "approve", comment="Bulk approved",
                                       expected_sequence=req.current_sequence)
    except approvals.ApprovalError as exc:
        messages.warning(request, f"{req.object_repr}: {exc}")
```

### 5.3 React to approval events
`core.signals.approval_event` is sent after *submitted*, *approved* and *rejected*:
```python
from django.dispatch import receiver
from core.signals import approval_event

@receiver(approval_event, sender=YarnWorkOrder)
def on_wo_event(sender, instance, request, action, actor, **kwargs):
    if action == "approved":
        ...   # e.g. create the purchase order
```
No-code alternative: **Notification rules** (`/r/notification-rules/`) can fire on approval events and notify the submitter, the
approvers, users/groups, by bell, e-mail and/or internal mail ([MAIL.md](MAIL.md)).

### 5.4 Signatures on the detail page, PDF and Excel (no code)
Every record that has an approval shows a **signature row** at the bottom of its detail page (modal and full page) once it has been
submitted: **Prepared By** (the submitter) and one box per step (its *Signature label*), each with the signer's **signature image**
(user form › Profile › Signature), name and date/time; undecided steps show "Pending". A withdrawn/cancelled request shows no row.
The detail page's **PDF** / **Excel** buttons (`/r/<slug>/<pk>/export/pdf|xlsx/`) print the same record: fields as a grid, each
inline as a table, the signature row (with images) at the bottom — like a work order. Which buttons show, and when: §3 *Print &
export*, or in code `register_resource(..., detail_exports=("pdf",), detail_export_when="approved")`. Downloads need view access to
the record and are audited.

### 5.5 Print approval signatures in your own documents
```python
from core.approvals.signatures import approval_signatures
sigs = approval_signatures(wo, labels={"check": "Checked By", "audit": "Audited By", "approve": "Approved By"})
# -> [Signature("Prepared By", submitter, submitted_at), Signature("Checked By", approver, approved_at), ...]
```
Return them in a custom report's `ReportOutput.signatures`, or put them in `meta["signatures"]` of any PDF; they print as the
Prepared/Checked/Audited/Approved boxes at the bottom of the last page ([REPORT_BUILDER.md](REPORT_BUILDER.md) §6.6, §7).
Box headings: `labels` first, then each step's *Signature label*, then the step name. Each `Signature` carries the signer's signature
image (`image`, bytes; `images=False` skips them); `record_signatures(obj)` is the row the detail page uses.

### 5.6 Your own approval UI
Include the card in any template: `{% include "core/approvals/_panel.html" with panel=approval %}` where `approval` is what
`core.views.crud._approval_panel(user, obj)` returns, and post to `core:approval_submit` (`/approvals/<resource slug>/<pk>/submit|resubmit|cancel/`).

---

## 6. Settings & permissions
| Name | Default | Meaning |
|---|---|---|
| `CORE_SELF_APPROVAL` | `False` | Allow submitters to decide steps of their own requests. |
| `core.add_step_to_request` | — | Add steps to pending requests of **every** approval type. For one type only, tick the user under *May add steps* on that type instead. |
| `sync_approval_chains` | — | Management command: create missing DB rows for code chains (never edits). |

## 7. Troubleshooting
| Message | Cause |
|---|---|
| `This record type is not set up for approvals.` | No code target and no active approval type *applies to* the model (or it is for another company). |
| `This approval chain must have exactly one final step.` | Fix the type's steps in the app. |
| `You are not allowed to decide this step.` | See §2.3 — most often the user lacks the step permission or is the submitter. |
| `Approval rule 'x' is not registered.` | A step names a skip rule that no `register_skip_rule` call provides. |
| `This is already waiting for approval.` | One pending request per record. |

## 8. File map
| File | Role |
|---|---|
| `core/approvals/registry.py` | `register_chain`, `register_target`, `register_skip_rule`, app-configured targets, `sync_chain` |
| `core/approvals/service.py` | The flow and all permission checks |
| `core/approvals/conditions.py` | "Only when" conditions (wraps `core.search.conditions`) |
| `core/approvals/signatures.py` | Signature boxes for printed documents |
| `core/models/approvals.py` | Models and the `AuditedModel` / `CancelableModel` mixins |
| `core/views/approvals.py` | Queue, my requests, request page, submit/resubmit/cancel |
