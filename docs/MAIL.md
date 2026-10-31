# Internal Mailing System

A Gmail-like mailbox inside the application: users write to each other (To / Cc / Bcc, attachments), reply, reply-all and forward
in conversations, and organise their own copy of every message with stars, archive, trash, labels and automatic filters. The system
itself (notification rules, your code) can also send mail. Nothing leaves the server — this is not SMTP e-mail.

Code: `core/mail/service.py` · models: `core/models/mail.py` · views: `core/views/mail.py` · templates: `core/templates/core/mail/` ·
tests: `tests/test_mail.py`

---

## 1. How it works

* A message (`MailMessage`) is written **once**. Every participant — sender and each recipient — gets their own **`Mailbox` copy**
  holding *their* state: read, starred, archived, trashed, deleted-forever, labels. What one person does never affects the others.
* **Conversations:** a reply points at the first message of the thread, so the thread page shows the whole conversation oldest first.
* **Folders are views over your copies:**

| Folder | Shows |
|---|---|
| **Inbox** | Received, not archived, not in trash |
| **Starred** | Starred, not in trash |
| **Sent** | Messages you wrote |
| **All** | Everything not in trash |
| **Trash** | Trashed copies (restore or delete forever from here) |
| **Label** | Copies carrying that label (not in trash) |

* **Company independent:** mail shows in every company. Each message stores `company` = where it comes from (the sender's
  active company; for system mail the record's company) and the inbox / thread show its abbreviation next to the sender.
  Notifications work the same way (`Notification.company`, badge in the bell and on the Notifications page).
* **Who can write to whom:** active users who share at least one company with you; superusers can write to any active user; the
  system can write to anyone (recipients are filtered by the caller).
* **Bcc** recipients are visible only to the sender (and each Bcc recipient sees themselves).
* **Bodies are plain text** — always escaped when shown, so mail cannot carry scripts or HTML.
* **Attachments** are validated (extension, size, image content) and stored in the private file store; only people holding a copy of
  a message that carries the file can download it (`Content-Disposition: attachment`, `nosniff`, sandboxed CSP).
* **Unread badge** in the top bar refreshes every 60 seconds.

Limits: 100 recipients, 10 attachments, 20,000 characters per body, 200 per subject; attachment size/types follow
`CORE_UPLOAD_MAX_BYTES` and `CORE_UPLOAD_ALLOWED_EXTENSIONS`.

---

## 2. Using it (end users)

Open **Mail** in the menu (`/mail/`).

### Writing
**Compose** → pick people in **To** (and optionally **Cc**, **Bcc** — *hidden from the others*; hold Ctrl/⌘ to pick several),
write a **Subject** and/or **Message**, add **Attachments**, **Send**. A link like `/mail/compose/?to=12&subject=Hello` opens the
form pre-filled.

### Reading a conversation
Click a message. The whole thread opens and is marked read. Actions: **Reply**, **Reply all**, **Forward** (choose people, add an
optional note — the original text and attachments are included), **Mark unread**, **Archive**, **Trash** / **Restore**, **Add label**
/ **Remove**. Mail sent by the system with a related record shows **Open the record**. System messages cannot be answered with plain
Reply.

### Managing many messages
In any folder tick messages and use: **Mark read**, **Mark unread**, **Star**, **Archive**, **Move to inbox**, **Trash**, **Restore**,
**Delete forever** (only from Trash), **Add label**, **Remove label**. **Search** finds text in subject, body or sender name.

### Labels
**Labels → Manage** (`/mail/labels/`): create labels with a colour, rename or delete them. Labels appear in the sidebar with unread
counts. A message can carry several labels.

### Filters (automatic sorting)
**Filters** (`/mail/filters/`): *When a message matches* — **From** (people), **From the system (rules, approvals)**, **Subject
contains**, **Text contains** (subject or body) — *Do this* — **Apply label**, **Mark as read**, **Star it**, **Skip the inbox
(archive)**. All filled-in criteria must match; a filter with no criteria never matches. Filters run on every incoming message in
order; **Apply now** runs one over the mail you already have. Untick **Active** to pause a filter.

---

## 3. System mail: notification rules (no code)

Administrators can make the system send internal mail when something happens, under **Notification rules**
(`/r/notification-rules/`): choose the model (*applies to*), the event (*created*, *changed*, *deleted*, *sent for approval*,
*approved*, *rejected*), an optional *only for records matching* condition (search-builder format), the recipients (users, groups, the
record's creator, the approval submitter / approvers, a user field on the record, the actor) and the title/body (with `{placeholders}`),
then tick **send as internal mail** (in addition to, or instead of, the bell and e-mail). Mail is sent only after the change is
committed, and recipients are always limited to active members of the record's company who may view that model.

---

## 4. Extending it (developers)

### 4.1 Send mail from code
```python
from core.mail.service import MailError, send, send_system

# as a user (checks who they may reach; raises MailError with a readable message)
message = send(request.user, to=[buyer.pk], cc=[manager], subject="WO 17 ready", body="Please check.",
               files=request.FILES.getlist("files"), url="/r/yarn-work-orders/17/")

# as the system (no sender; you decide the recipients)
from django.db import transaction
transaction.on_commit(lambda: send_system(approvers, "Approval needed: WO 17", "Step: Checked By", url=wo_url))
```
* `to` / `cc` / `bcc` accept user ids or user objects. A person appearing twice gets one copy with the most visible role.
* `url` must be a site-relative path (`/…`); anything else is dropped. It becomes the **Open the record** button.
* Send inside `transaction.on_commit` when it reacts to a database change, so a rolled-back change sends nothing.
* `send_system` skips inactive users and returns `None` when nobody is left.

### 4.2 Read and act on mailboxes
| Function | Purpose |
|---|---|
| `folder(user, "inbox"\|"starred"\|"sent"\|"all"\|"trash", label=None, q="")` | The user's copies in a folder (a queryset). |
| `thread_copies(user, copy)` | The user's copies of a conversation, oldest first. |
| `participants(copy)` | `{"to", "cc", "bcc"}` as that copy's owner may see them. |
| `reply(user, copy, body, everyone=False, files=())`, `forward(user, copy, to, body="", files=())` | Answer / pass on. |
| `bulk(user, ids, action, label=None)` | `read`, `unread`, `star`, `unstar`, `archive`, `inbox`, `trash`, `restore`, `delete`, `label`, `unlabel` — only on the user's own copies. |
| `unread_count(user)`, `label_counts(user)` | Badges. |
| `apply_filters(copy)`, `apply_filter_to_existing(filter)` | Run filters. |
| `reachable_users(user)` | Who the user may write to (use it for your own pickers). |
| `can_download(user, stored_file)` | Attachment access check. |

### 4.3 Example: a "Mail this report" button
```python
from django.core.files.base import ContentFile
from core.mail.service import send
from core.reports.custom import custom_report_bytes, execute, get_custom_report

report = get_custom_report("yarn-work-order")
output = execute(report, ctx, {"number": 17})
data, name, _ = custom_report_bytes(report, output, ctx, "pdf")
send(request.user, to=[supplier_contact.pk], subject=output.title, body="Work order attached.",
     files=[ContentFile(data, name=name)])
```

### 4.4 Common customisations
* **Who can write to whom:** change `reachable_users()` in `core/mail/service.py` (e.g. restrict to the same department). Every send,
  reply and forward goes through it.
* **New filter criteria or actions:** add fields to `MailFilter` (+ migration), check them in `_matches()` / apply them in `_act()`,
  and add them to `FilterForm` in `core/views/mail.py`.
* **A new folder:** add its name to `FOLDERS` and a branch in `folder()`; the URL `/mail/<name>/` picks it up.
* **Limits:** `MAX_RECIPIENTS`, `MAX_ATTACHMENTS`, `MAX_BODY` at the top of `core/mail/service.py`.

---

## 5. Data model
| Model | Key fields |
|---|---|
| `MailMessage` | `sender` (null = system), `sender_name`, `subject`, `body`, `thread` (first message), `in_reply_to`, `url`, `created_at` |
| `Mailbox` | `owner`, `message`, `role` (`to`/`cc`/`bcc`/`from`), `is_read`, `is_starred`, `is_archived`, `trashed_at`, `is_deleted`, `labels` |
| `MailAttachment` | `message`, `file` (→ `StoredFile`, private storage) |
| `MailLabel` | `owner`, `name` (unique per owner), `color` |
| `MailFilter` | `owner`, `name`, `from_users`, `from_system`, `subject_contains`, `body_contains`, `label`, `mark_read`, `star`, `skip_inbox`, `is_active`, `position` |

## 6. URLs
| Name | Path |
|---|---|
| `core:mail_inbox` / `core:mail_folder` | `/mail/`, `/mail/<starred\|sent\|all\|trash>/` |
| `core:mail_label` | `/mail/label/<id>/` |
| `core:mail_thread` | `/mail/m/<copy id>/` |
| `core:mail_compose` | `/mail/compose/` |
| `core:mail_labels`, `core:mail_filters`, `core:mail_filter` | `/mail/labels/`, `/mail/filters/`, `/mail/filters/<id>/` |
| `core:mail_file` | `/mail/file/<uuid>/` (attachment download) |
| `core:mail_badge` | `/mail/badge/` (unread badge fragment) |
