"""Notification rules built in the app (``NotificationRule``): model events -> notices in the top-bar bell (+ optional e-mail).

Events come from Django's model signals (so any model works, with no mixin and no code), ``post_soft_delete`` and the
approval engine's ``approval_event``. Only models that have an active rule do any work: their content types are cached and the
cache is dropped whenever a rule changes. A failing rule is logged and skipped -- it never breaks the save that triggered it.

Recipients are always filtered to active users who belong to the record's company and may view the model, so a rule can never
leak a record to someone who could not open it. Messages use ``{placeholder}`` substitution only (no attribute access).
"""
from __future__ import annotations

import logging
import re

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.signals import approval_event, post_soft_delete

log = logging.getLogger("core.notifications")
CACHE_KEY = "core:notification_rule_cts"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
BASE_PLACEHOLDERS = ("record", "model", "event", "actor", "company")
EVENT_WORDS = {"created": "created", "updated": "changed", "deleted": "deleted", "approval_submitted": "sent for approval",
               "approval_approved": "approved", "approval_rejected": "rejected"}
APPROVAL_ACTIONS = {"submitted": "approval_submitted", "approved": "approval_approved", "rejected": "approval_rejected"}


# --------------------------------------------------------------------------------------------------------------- rule checks
def _resource(model):
    from core.crud.registry import resource_for_model

    return resource_for_model(model) if model is not None else None


def placeholder_names(model) -> set[str]:
    from core.audit.redaction import is_sensitive_name

    names = set(BASE_PLACEHOLDERS)
    if model is not None:
        names |= {f.name for f in model._meta.concrete_fields if not is_sensitive_name(f.name)}
    return names


def validate_rule(rule) -> dict:
    """Field errors for the admin form (empty dict = valid). Also normalises the condition in place."""
    from django.conf import settings

    from core.search import conditions
    from core.search.errors import SearchError

    from core.models import NotificationRule

    errors: dict = {}
    model = rule.content_type.model_class() if rule.content_type_id else None
    if model is not None and _resource(model) is None:
        errors["content_type"] = "Choose a model that has a list page."
    concrete = {f.name: f for f in model._meta.concrete_fields} if model is not None else {}
    watched = rule.watched()
    if watched and rule.event != NotificationRule.UPDATED:
        errors["watch_fields"] = "Watched fields only apply to “Record changed”."
    bad = [n for n in watched if n not in concrete]
    if bad and model is not None:
        errors["watch_fields"] = f"Not fields of {model._meta.verbose_name}: {', '.join(bad)}."
    if rule.user_field:
        f = concrete.get(rule.user_field)
        if model is not None and (f is None or not f.is_relation or f.related_model is None
                                  or f.related_model._meta.label != settings.AUTH_USER_MODEL):
            errors["user_field"] = f"{rule.user_field!r} is not a user field of {model._meta.verbose_name}."
    if rule.notify_submitter and rule.event not in NotificationRule.APPROVAL_EVENTS:
        errors["notify_submitter"] = "Only approval events have a submitter."
    if rule.notify_approvers and rule.event not in NotificationRule.APPROVAL_EVENTS:
        errors["notify_approvers"] = "Only approval events have approvers."
    if not (rule.send_in_app or rule.send_mail or rule.send_email):
        errors["send_in_app"] = "Choose at least one way to deliver it (bell, internal mail or e-mail)."
    try:
        rule.condition = conditions.validate(model, rule.condition) if rule.condition else {}
    except SearchError as exc:
        errors["condition"] = str(exc)
    allowed = placeholder_names(model)
    for name in ("title", "body"):
        unknown = sorted(set(PLACEHOLDER.findall(getattr(rule, name) or "")) - allowed)
        if unknown and model is not None:
            errors[name] = "Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in unknown) + "."
    return errors


# --------------------------------------------------------------------------------------------------------------- delivery
def active_content_types() -> set[int]:
    cts = cache.get(CACHE_KEY)
    if cts is None:
        from django.db import DatabaseError

        from core.models import NotificationRule

        try:
            with transaction.atomic():                          # savepoint: on PostgreSQL a failed query would otherwise
                cts = set(NotificationRule.objects.filter(is_active=True).values_list("content_type_id", flat=True))
        except DatabaseError:                                   # abort the caller's transaction (table not migrated yet)
            return set()
        cache.set(CACHE_KEY, cts, 3600)
    return cts


def invalidate(**kwargs) -> None:
    cache.delete(CACHE_KEY)


def _ct_id(model) -> int | None:
    try:
        with transaction.atomic():                              # savepoint, see active_content_types
            return ContentType.objects.get_for_model(model, for_concrete_model=False).pk
    except Exception:  # noqa: BLE001 - models without content types (migrations in progress)
        return None


IGNORED_MODELS = {"contenttypes.ContentType", "sessions.Session", "core.Notification", "core.NotificationRule", "core.AuditLog",
                  "migrations.Migration"}


def _watching(model) -> bool:
    if model._meta.label in IGNORED_MODELS:                     # bookkeeping (and the content-type lookup itself)
        return False
    if model._meta.apps is not global_apps:                     # historical model saved by a migration (RunPython)
        return False
    cts = active_content_types()
    if not cts:
        return False                                            # the common case: no rules at all, no further work
    ct = _ct_id(model)
    return ct is not None and ct in cts


def render(template: str, values: dict) -> str:
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template or "")


def _values(rule, obj, event: str, actor) -> dict:
    from core.audit.redaction import is_sensitive_name
    from core.audit.service import _as_stored
    from core.crud.service import display_value

    values = {"record": str(obj), "model": str(obj._meta.verbose_name).capitalize(), "event": EVENT_WORDS.get(event, event),
              "actor": (getattr(actor, "full_name", "") or getattr(actor, "email", "")) if actor is not None else "System",
              "company": str(getattr(obj, "company", "") or "") if hasattr(obj, "company_id") else ""}
    used = set(PLACEHOLDER.findall(rule.title or "")) | set(PLACEHOLDER.findall(rule.body or ""))
    for f in obj._meta.concrete_fields:
        if f.name in used and f.name not in values and not is_sensitive_name(f.name):
            try:
                plain = not f.is_relation and not getattr(f, "choices", None)
                values[f.name] = _as_stored(f, getattr(obj, f.attname)) if plain else display_value(obj, f.name)
            except Exception:  # noqa: BLE001 - a dangling FK on a deleted row
                values[f.name] = ""
    return values


def _creator_id(obj):
    """``created_by`` when the model has it, else the user of the record's "create" audit row (audited models)."""
    if hasattr(obj, "created_by_id"):
        return obj.created_by_id
    from core.models import AuditLog

    return (AuditLog.objects.filter(event=AuditLog.CREATE, model_label=obj._meta.label, object_pk=str(obj.pk))
            .order_by("pk").values_list("user_id", flat=True).first())


def _recipients(rule, obj, actor, approval, company_id, alive: bool) -> list:
    from core.crud.access import allowed

    User = get_user_model()
    ids = set(rule.notify_users.values_list("pk", flat=True))
    ids |= set(User.objects.filter(groups__in=rule.notify_groups.all()).values_list("pk", flat=True))
    if rule.notify_creator and _creator_id(obj):
        ids.add(_creator_id(obj))
    if rule.user_field and getattr(obj, f"{rule.user_field}_id", None):
        ids.add(getattr(obj, f"{rule.user_field}_id"))
    if rule.notify_submitter and approval is not None and approval.submitted_by_id:
        ids.add(approval.submitted_by_id)
    if rule.notify_approvers and approval is not None:
        ids |= set(approval.history.filter(action="approve", actor__isnull=False).values_list("actor_id", flat=True))
    actor_id = getattr(actor, "pk", None)
    if rule.notify_actor and actor_id:
        ids.add(actor_id)
    elif actor_id:
        ids.discard(actor_id)                                   # people are not told about what they just did themselves
    people = User.objects.filter(pk__in=ids, is_active=True)
    if company_id:
        people = people.filter(profile__companies=company_id)
    cfg = _resource(type(obj))
    perm = cfg.perm("view") if cfg else None
    return [u for u in people.distinct() if allowed(u, perm, obj if alive else None)]


def _url(obj, alive: bool) -> str:
    from django.urls import NoReverseMatch, reverse

    cfg = _resource(type(obj))
    if cfg is None:
        return ""
    try:
        return reverse("core:resource_detail", args=[cfg.slug, obj.pk]) if alive else reverse("core:resource_list", args=[cfg.slug])
    except NoReverseMatch:
        return ""


def _run_rule(rule, obj, event, actor, changed, approval, alive, company_id) -> bool:
    """One rule for one event; True when it sent something."""
    from core.models import NotificationRule
    from core.search import conditions

    from .service import notify

    model = type(obj)
    if rule.company_id and rule.company_id != company_id:
        return False
    if event == NotificationRule.UPDATED:
        watched = set(rule.watched())
        if not changed or (watched and not watched & changed):
            return False
    tree = conditions.normalize(rule.condition)
    if tree.get("items"):
        if not alive and not model._base_manager.filter(pk=obj.pk).exists():
            return False                                # a hard-deleted row can no longer be checked
        if not conditions.matches(obj, tree):
            return False
    people = _recipients(rule, obj, actor, approval, company_id, alive)
    if not people:
        return False
    values = _values(rule, obj, event, actor)
    title, body, url = render(rule.title, values)[:200], render(rule.body, values)[:500], _url(obj, alive)
    if rule.send_in_app or rule.send_email:
        notify(people, title, body=body, url=url, level=rule.level, kind=f"rule:{rule.pk}"[:40], company=company_id,
               email=True if rule.send_email else None, in_app=rule.send_in_app)
    if rule.send_mail:
        _mail_on_commit(people, title, body, url, company_id)
    return True


def fire(event: str, obj, *, actor=None, changed: set | None = None, approval=None, alive: bool = True) -> int:
    """Run every active rule of ``obj``'s model for ``event``. Returns how many rules sent something."""
    from core.models import NotificationRule
    from core.approvals.registry import _record_company

    model = type(obj)
    if not _watching(model):
        return 0
    company_id = _record_company(obj)
    sent = 0
    rules = (NotificationRule.objects.filter(is_active=True, content_type_id=_ct_id(model), event=event)
             .prefetch_related("notify_users", "notify_groups"))
    for rule in rules:
        try:
            with transaction.atomic():                          # a failing rule rolls back only its own savepoint
                if _run_rule(rule, obj, event, actor, changed, approval, alive, company_id):
                    sent += 1
        except Exception:  # noqa: BLE001 - a broken rule must never break the business operation
            log.exception("notification rule %s failed", rule.pk)
    return sent


def _mail_on_commit(people, title: str, body: str, url: str, company_id=None) -> None:
    from django.db import transaction

    from core.mail.service import send_system

    transaction.on_commit(lambda: send_system(people, title, body, url, company=company_id))     # a rolled-back change sends nothing


# --------------------------------------------------------------------------------------------------------------- receivers
def _actor():
    from core.services import context

    return context.current_user()


@receiver(pre_save, dispatch_uid="core_rules_pre_save")
def remember_old(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk or instance._state.adding or not _watching(sender):
        return
    from core.audit.service import snapshot

    old = sender._base_manager.filter(pk=instance.pk).first()
    instance._rules_old = snapshot(old) if old is not None else None


@receiver(post_save, dispatch_uid="core_rules_post_save")
def on_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _watching(sender):
        return
    if created:
        fire("created", instance, actor=_actor())
        return
    from core.audit.service import snapshot

    old, new = getattr(instance, "_rules_old", None) or {}, snapshot(instance)
    changed = {k for k in set(old) | set(new) if old.get(k) != new.get(k)} - {"updated_at", "updated_by", "deleted_at", "deleted_by"}
    instance._rules_old = new
    if changed:
        fire("updated", instance, actor=_actor(), changed=changed)


@receiver(post_soft_delete, dispatch_uid="core_rules_soft_delete")
def on_soft_delete(sender, instance, user=None, **kwargs):
    fire("deleted", instance, actor=user or _actor(), alive=False)


@receiver(post_delete, dispatch_uid="core_rules_hard_delete")
def on_delete(sender, instance, **kwargs):
    if _watching(sender):
        fire("deleted", instance, actor=_actor(), alive=False)


@receiver(approval_event, dispatch_uid="core_rules_approval")
def on_approval(sender, instance, request=None, action="", actor=None, **kwargs):
    event = APPROVAL_ACTIONS.get(action)
    if event:
        fire(event, instance, actor=actor, approval=request)


def connect_invalidation() -> None:
    from core.models import NotificationRule

    post_save.connect(invalidate, sender=NotificationRule, dispatch_uid="core_rules_invalidate_save", weak=False)
    post_delete.connect(invalidate, sender=NotificationRule, dispatch_uid="core_rules_invalidate_delete", weak=False)
