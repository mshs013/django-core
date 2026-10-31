"""In-app notifications (+ optional email, optional Channels). Delivery is ALWAYS scheduled with ``transaction.on_commit`` so a
rolled-back business transaction never notifies anyone."""
from __future__ import annotations

import logging
from typing import Iterable

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from core import conf

log = logging.getLogger("core.notifications")


def notify(recipients: Iterable, title: str, *, body: str = "", url: str = "", level: str = "info", kind: str = "", company=None,
           email: bool | None = None, in_app: bool = True) -> None:
    """``email``: True/False forces the e-mail copy on/off for this notice; None follows ``CORE_NOTIFY_EMAIL``.
    ``in_app=False`` skips the bell (e-mail only)."""
    users = [u for u in {getattr(r, "pk", None): r for r in recipients if r is not None}.values() if getattr(u, "is_active", False)]
    if not users:
        return
    company_id = getattr(company, "pk", company)
    safe_url = url if url.startswith("/") and not url.startswith("//") else ""

    def deliver():
        from core.models import Notification

        rows = Notification.objects.bulk_create([
            Notification(recipient=u, company_id=company_id, title=title[:200], body=body[:500], url=safe_url[:300], level=level, kind=kind[:40])
            for u in users]) if in_app else []
        if conf.get("CORE_NOTIFY_EMAIL") if email is None else email:
            _email(users, title, body)
        if conf.get("CORE_CHANNELS_LIVE"):
            _push(users, title, body, safe_url)
        return rows

    transaction.on_commit(deliver)


def _email(users, title, body):
    try:
        from django.core.mail import send_mass_mail

        send_mass_mail([(title[:200], body or title, None, [u.email]) for u in users if u.email], fail_silently=True)
    except Exception:  # noqa: BLE001
        log.exception("notification email failed")


def _push(users, title, body, url):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        for u in users:
            async_to_sync(layer.group_send)(f"core_user_{u.pk}", {"type": "core.notify", "title": title, "body": body, "url": url})
    except ImportError:
        log.info("CORE_CHANNELS_LIVE is on but channels is not installed; skipping live push")
    except Exception:  # noqa: BLE001
        log.exception("live notification push failed")


def superusers():
    return get_user_model().objects.filter(is_superuser=True, is_active=True)


def unread_count(user) -> int:
    from core.models import Notification

    return Notification.objects.filter(recipient=user, is_read=False).count() if getattr(user, "is_authenticated", False) else 0


def mark_read(user, ids=None) -> int:
    """Mark the user's notifications read (``ids`` None = all). Only ever touches the caller's own rows."""
    from django.utils import timezone

    from core.models import Notification

    qs = Notification.objects.filter(recipient=user, is_read=False)
    if ids is not None:
        qs = qs.filter(pk__in=[int(i) for i in ids if str(i).isdigit()])
    return qs.update(is_read=True, read_at=timezone.now())
