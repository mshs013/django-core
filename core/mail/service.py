"""Internal mail service: send / reply / forward, per-user folders, labels, filters, bulk actions.

Rules: people can write to active users who share a company with them (superusers to anyone active); the system (notification
rules) can write to anyone. Every participant gets their own ``Mailbox`` copy, so read/star/trash/labels are personal. Bcc
recipients are visible only to the sender. Bodies are plain text (escaped when shown). Attachments go to the private store.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import MailAttachment, MailFilter, MailLabel, Mailbox, MailMessage

MAX_RECIPIENTS = 100
MAX_ATTACHMENTS = 10
MAX_BODY = 20000
FOLDERS = ("inbox", "starred", "sent", "all", "trash")


class MailError(Exception):
    pass


def display_name(user) -> str:
    return (getattr(user, "full_name", "") or getattr(user, "email", "")) if user is not None else "System"


def reachable_users(user):
    """Who ``user`` may write to."""
    from core.permissions import user_company_ids

    qs = get_user_model().objects.filter(is_active=True)
    if user is None or user.is_superuser:
        return qs.order_by("email")
    return qs.filter(profile__companies__in=user_company_ids(user)).distinct().order_by("email")


def _people(sender, ids) -> list:
    ids = {int(i) for i in ids if str(i).isdigit()}
    found = list(reachable_users(sender).filter(pk__in=ids))
    if len(found) != len(ids):
        raise MailError("Some recipients are not available to you.")
    return found


# --------------------------------------------------------------------------------------------------------------- sending
def send(sender, *, to=(), cc=(), bcc=(), subject: str = "", body: str = "", files=(), reply_to: MailMessage | None = None,
         url: str = "", forward_attachments=(), company=None) -> MailMessage:
    """``to``/``cc``/``bcc`` are user ids (or users). ``sender`` None = system mail. ``company`` (instance or id) is where the
    message comes from; empty = the sender's active company. Returns the message."""
    def ids(values):
        return [getattr(v, "pk", v) for v in values]

    to_users, cc_users, bcc_users = _people(sender, ids(to)), _people(sender, ids(cc)), _people(sender, ids(bcc))
    everyone = {u.pk for u in to_users + cc_users + bcc_users}
    if not everyone:
        raise MailError("Add at least one recipient.")
    if len(everyone) > MAX_RECIPIENTS:
        raise MailError(f"A message can go to at most {MAX_RECIPIENTS} people.")
    subject, body = (subject or "").strip()[:200], (body or "")
    if not subject and not body.strip():
        raise MailError("Write a subject or a message.")
    if len(body) > MAX_BODY:
        raise MailError(f"The message is too long (maximum {MAX_BODY} characters).")
    files = list(files)
    if len(files) + len(list(forward_attachments)) > MAX_ATTACHMENTS:
        raise MailError(f"At most {MAX_ATTACHMENTS} attachments.")
    safe_url = url if url.startswith("/") and not url.startswith("//") else ""
    company_id = getattr(company, "pk", company)
    if company_id is None and sender is not None:
        from core.permissions import active_company_id

        company_id = active_company_id(sender)
    with transaction.atomic():
        thread = (reply_to.thread or reply_to) if reply_to is not None else None
        message = MailMessage.objects.create(sender=sender, sender_name=display_name(sender), subject=subject, body=body,
                                             thread=thread, in_reply_to=reply_to, url=safe_url[:300], company_id=company_id)
        _attach(message, sender, files, forward_attachments)
        if sender is not None:
            Mailbox.objects.create(owner=sender, message=message, role=Mailbox.FROM, is_read=True)
        seen = set()
        for role, users in ((Mailbox.TO, to_users), (Mailbox.CC, cc_users), (Mailbox.BCC, bcc_users)):
            for user in users:
                if user.pk in seen:
                    continue                                    # once per person, with the most visible role
                seen.add(user.pk)
                copy = Mailbox.objects.create(owner=user, message=message, role=role)
                apply_filters(copy)
    return message


def send_system(recipients, subject: str, body: str = "", url: str = "", company=None) -> MailMessage | None:
    """Mail from the system (notification rules, approvals). Recipients already filtered by the caller; ``company`` = the company
    of the record it is about."""
    people = [u for u in recipients if getattr(u, "is_active", False)]
    if not people:
        return None
    return send(None, to=people, subject=subject, body=body, url=url, company=company)


def _attach(message, sender, files, forwarded) -> None:
    from django.core.exceptions import ValidationError

    from core.files.service import save_upload

    for upload in files:
        try:
            stored = save_upload(upload, user=sender, target=message)
        except ValidationError as exc:
            raise MailError(f"{getattr(upload, 'name', 'File')}: {' '.join(exc.messages)}") from None
        MailAttachment.objects.create(message=message, file=stored)
    for attachment in forwarded:
        MailAttachment.objects.create(message=message, file=attachment.file)


def participants(copy: Mailbox) -> dict:
    """{"to": [...], "cc": [...], "bcc": [...]} as the owner of ``copy`` may see them (Bcc only for the sender)."""
    rows = Mailbox.objects.filter(message=copy.message).exclude(role=Mailbox.FROM).select_related("owner")
    out = {"to": [], "cc": [], "bcc": []}
    for row in rows:
        if row.role == Mailbox.BCC and not (copy.role == Mailbox.FROM or row.owner_id == copy.owner_id):
            continue
        out[row.role].append(row.owner)
    return out


def reply(user, copy: Mailbox, body: str, *, everyone: bool = False, files=()) -> MailMessage:
    message = copy.message
    if message.sender_id is None and not everyone:
        raise MailError("System messages cannot be answered.")
    seen = participants(copy)
    if copy.role == Mailbox.FROM:
        to = [u.pk for u in seen["to"]]
        cc = [u.pk for u in seen["cc"]] if everyone else []
    else:
        to = [message.sender_id] if message.sender_id else []
        cc = [u.pk for u in seen["to"] + seen["cc"] if u.pk != user.pk] if everyone else []
    to = [i for i in to if i != user.pk] or to
    subject = message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
    return send(user, to=to, cc=[i for i in cc if i not in to], subject=subject, body=body, files=files, reply_to=message)


def forward(user, copy: Mailbox, to, body: str = "", *, files=()) -> MailMessage:
    message = copy.message
    quoted = (f"\n\n---------- Forwarded message ----------\nFrom: {message.sender_name or 'System'}\n"
              f"Date: {timezone.localtime(message.created_at):%Y-%m-%d %H:%M}\nSubject: {message.subject}\n\n{message.body}")
    subject = message.subject if message.subject.lower().startswith("fwd:") else f"Fwd: {message.subject}"
    return send(user, to=to, subject=subject, body=(body or "") + quoted, files=files,
                forward_attachments=list(message.attachments.select_related("file")))


# --------------------------------------------------------------------------------------------------------------- filters
def _matches(flt: MailFilter, message: MailMessage) -> bool:
    criteria = False
    if flt.from_system or flt.pk and flt.from_users.exists():
        criteria = True
        from_ok = (flt.from_system and message.sender_id is None) or (
            message.sender_id is not None and flt.from_users.filter(pk=message.sender_id).exists())
        if not from_ok:
            return False
    if flt.subject_contains:
        criteria = True
        if flt.subject_contains.lower() not in message.subject.lower():
            return False
    if flt.body_contains:
        criteria = True
        text = flt.body_contains.lower()
        if text not in message.body.lower() and text not in message.subject.lower():
            return False
    return criteria                                            # a filter with no criteria never matches


def _act(flt: MailFilter, copy: Mailbox) -> None:
    if flt.label_id:
        copy.labels.add(flt.label_id)
    changed = []
    for flag, attr, value in ((flt.mark_read, "is_read", True), (flt.star, "is_starred", True), (flt.skip_inbox, "is_archived", True)):
        if flag and getattr(copy, attr) != value:
            setattr(copy, attr, value)
            changed.append(attr)
    if changed:
        copy.save(update_fields=changed)


def apply_filters(copy: Mailbox) -> None:
    for flt in MailFilter.objects.filter(owner=copy.owner, is_active=True):
        if _matches(flt, copy.message):
            _act(flt, copy)


def apply_filter_to_existing(flt: MailFilter) -> int:
    """"Apply now": run one filter over the owner's received mail. Returns how many messages matched."""
    count = 0
    for copy in Mailbox.objects.filter(owner=flt.owner, is_deleted=False).exclude(role=Mailbox.FROM).select_related("message"):
        if _matches(flt, copy.message):
            _act(flt, copy)
            count += 1
    return count


# --------------------------------------------------------------------------------------------------------------- folders
def folder(user, name: str = "inbox", *, label: MailLabel | None = None, q: str = ""):
    qs = Mailbox.objects.filter(owner=user, is_deleted=False).select_related("message", "message__sender", "message__company")
    if label is not None:
        qs = qs.filter(labels=label, trashed_at__isnull=True)
    elif name == "inbox":
        qs = qs.exclude(role=Mailbox.FROM).filter(is_archived=False, trashed_at__isnull=True)
    elif name == "starred":
        qs = qs.filter(is_starred=True, trashed_at__isnull=True)
    elif name == "sent":
        qs = qs.filter(role=Mailbox.FROM, trashed_at__isnull=True)
    elif name == "trash":
        qs = qs.filter(trashed_at__isnull=False)
    elif name == "all":
        qs = qs.filter(trashed_at__isnull=True)
    else:
        raise MailError("Unknown folder.")
    q = (q or "").strip()[:100]
    if q:
        qs = qs.filter(Q(message__subject__icontains=q) | Q(message__body__icontains=q) | Q(message__sender_name__icontains=q))
    return qs.prefetch_related("labels", "message__copies", "message__attachments").order_by("-message__created_at", "-pk")


def unread_count(user) -> int:
    if not getattr(user, "is_authenticated", False):
        return 0
    return folder(user, "inbox").filter(is_read=False).count()


def label_counts(user) -> dict:
    from django.db.models import Count

    rows = (Mailbox.objects.filter(owner=user, is_deleted=False, trashed_at__isnull=True, is_read=False).exclude(role=Mailbox.FROM)
            .values("labels").annotate(n=Count("pk")))
    return {r["labels"]: r["n"] for r in rows if r["labels"]}


def thread_copies(user, copy: Mailbox):
    """The user's copies of every message in ``copy``'s conversation, oldest first."""
    root = copy.message.thread_id or copy.message_id
    return (Mailbox.objects.filter(owner=user, is_deleted=False).filter(Q(message_id=root) | Q(message__thread_id=root))
            .select_related("message", "message__company").prefetch_related("message__attachments__file").order_by("message__created_at", "pk"))


ACTIONS = ("read", "unread", "star", "unstar", "archive", "inbox", "trash", "restore", "delete", "label", "unlabel")


def bulk(user, ids, action: str, label: MailLabel | None = None) -> int:
    """Apply ``action`` to the user's own copies ``ids`` (other people's copies are never touched)."""
    if action not in ACTIONS:
        raise MailError("Unknown action.")
    if label is not None and label.owner_id != user.pk:
        raise MailError("Unknown label.")
    qs = Mailbox.objects.filter(owner=user, is_deleted=False, pk__in=[int(i) for i in ids if str(i).isdigit()])
    updates = {"read": {"is_read": True}, "unread": {"is_read": False}, "star": {"is_starred": True}, "unstar": {"is_starred": False},
               "archive": {"is_archived": True}, "inbox": {"is_archived": False, "trashed_at": None}, "trash": {"trashed_at": timezone.now()},
               "restore": {"trashed_at": None}}
    if action in updates:
        return qs.update(**updates[action])
    if action == "delete":
        return qs.filter(trashed_at__isnull=False).update(is_deleted=True)     # "delete forever" only from the trash
    if label is None:
        raise MailError("Choose a label.")
    copies = list(qs)
    for copy in copies:
        (copy.labels.add if action == "label" else copy.labels.remove)(label)
    return len(copies)


def can_download(user, stored) -> bool:
    """An attachment may be downloaded by anyone holding a (not deleted-forever) copy of a message that carries it."""
    return Mailbox.objects.filter(owner=user, is_deleted=False, message__attachments__file=stored).exists()
