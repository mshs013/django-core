"""The seeding engine: fills every registered model with plausible rows, in dependency order, and can remove exactly what it made.

``run()`` steps
1. companies (existing ones are reused; ``companies=N`` adds more), then ``count`` departments/designations (shared by every company);
2. users with profiles, a "Seed: business access" role (every permission of the seeded business models) and "all rows" object
   access for each access-scoped model, so the seeded people can actually use what was seeded;
3. every other seedable model (``registry.seedable_models``), parents before children, with their inlines (nested too);
4. optional *activity*: approval requests submitted/decided by the seeded users (which also raises notifications) and mail.

Every created row is written to ``SeedRecord`` under one batch id; ``purge()`` deletes those rows, newest first.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import uuid
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils import timezone

from core.services.context import acting_as

from . import values
from .models import SeedRecord
from .registry import seedable_models, spec_for

log = logging.getLogger("seeder")

PASSWORD = "demo-pass-12345"
ROLE_NAME = "Seed: business access"
SYSTEM_FIELDS = {"deleted_at", "deleted_by", "is_confirm", "is_approval", "approved_at", "is_cancel", "canceled_by", "canceled_at",
                 "created_at", "created_by", "updated_at", "updated_by", "password", "last_login"}
TRIES = 5


class Unseedable(Exception):
    """The model cannot be filled automatically (the message says why); register a seeder ``factory`` for it."""


@dataclass
class Result:
    batch: str
    created: dict = field(default_factory=dict)     # label -> rows
    skipped: dict = field(default_factory=dict)     # label -> reason
    activity: dict = field(default_factory=dict)    # what -> count

    def add(self, label: str, n: int = 1) -> None:
        self.created[label] = self.created.get(label, 0) + n

    @property
    def total(self) -> int:
        return sum(self.created.values())


def enabled() -> bool:
    """``SEEDER_ENABLED`` (default: on with DEBUG or in tests). The web page and the command refuse to write otherwise."""
    from django.conf import settings

    return bool(getattr(settings, "SEEDER_ENABLED", settings.DEBUG or getattr(settings, "TESTING", False)))


def label(model) -> str:
    return f"{model._meta.app_label}.{model._meta.object_name}"


def _company_model():
    from core.models import Company

    return Company


def _company_fk(model):
    """The FK to Company that scopes ``model`` (``company``), or None."""
    Company = _company_model()
    for f in model._meta.concrete_fields:
        if f.is_relation and f.related_model is Company and f.name == "company":
            return f
    return None


def _alive(qs):
    model = qs.model
    return qs.filter(deleted_at__isnull=True) if any(f.name == "deleted_at" for f in model._meta.concrete_fields) else qs


class Ctx:
    """State of one run, handed to registered ``fields`` callables and factories as ``ctx``."""

    def __init__(self, *, rng: random.Random, batch: str, count: int, result: Result):
        self.rng, self.batch, self.count, self.result = rng, batch, count, result
        self.companies: list = []
        self.users: list = []
        self._serial: dict = {}
        self._pools: dict = {}

    # -- helpers for registered seeders
    def serial(self, model) -> int:
        """1, 2, 3… per model, continuing after the rows already in the table (keeps codes and e-mails unique across runs)."""
        if model not in self._serial:
            self._serial[model] = model._base_manager.count()
        self._serial[model] += 1
        return self._serial[model]

    def company(self):
        return self.rng.choice(self.companies) if self.companies else None

    def user(self, company=None):
        people = [u for u in self.users if company is None or company.pk in self._user_companies(u)] or self.users
        return self.rng.choice(people) if people else None

    def pick(self, model, company=None):
        """A random existing row of ``model`` (same company when ``model`` is company scoped), or None."""
        pool = self._pool(model, company)
        return self.rng.choice(pool) if pool else None

    # -- internals
    def _user_companies(self, user) -> set:
        if not hasattr(user, "_seed_companies"):
            user._seed_companies = set(user.profile.companies.values_list("pk", flat=True))
        return user._seed_companies

    def _pool(self, model, company=None) -> list:
        key = (model, getattr(company, "pk", None) if _company_fk(model) else None)
        if key not in self._pools:
            qs = _alive(model._base_manager.all())
            if key[1] is not None:
                qs = qs.filter(company_id=key[1])
            self._pools[key] = list(qs.order_by("-pk")[:200])
        return self._pools[key]

    def remember(self, obj) -> None:
        model = type(obj)
        for key, pool in self._pools.items():
            if key[0] is model and (key[1] is None or key[1] == getattr(obj, "company_id", None)):
                pool.append(obj)
        SeedRecord.objects.create(batch=self.batch, content_type=ContentType.objects.get_for_model(obj, for_concrete_model=False),
                                  object_id=str(obj.pk))


# --------------------------------------------------------------------------------------------------------------- ordering
def ordered(model_list) -> list:
    """Parents before children (FKs between the listed models); cycles keep their original order."""
    remaining = list(model_list)
    deps = {m: {f.related_model for f in m._meta.concrete_fields if f.is_relation and f.related_model in remaining and f.related_model is not m}
            for m in remaining}
    out: list = []
    while remaining:
        ready = [m for m in remaining if not (deps[m] - set(out))]
        nxt = ready[0] if ready else remaining[0]
        out.append(nxt)
        remaining.remove(nxt)
    return out


# --------------------------------------------------------------------------------------------------------------- generic rows
def _fields_to_fill(model):
    for f in model._meta.concrete_fields:
        if f.primary_key or isinstance(f, models.AutoField) or getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False):
            continue
        if f.name in SYSTEM_FIELDS:
            continue
        yield f


def _required(f) -> bool:
    return not f.blank and not f.has_default() and not f.null


def _relation_value(f, ctx: Ctx, obj, company):
    rel = f.related_model
    Company = _company_model()
    if rel is Company:
        return company if f.name == "company" or _required(f) else None
    if rel is get_user_model():
        return ctx.user(company)
    if rel is ContentType:
        raise Unseedable(f"{f.name} is a generic relation") if _required(f) else None
    if rel is type(obj) and not _required(f):
        return ctx.pick(rel, company) if ctx.rng.random() < 0.3 else None
    pool = ctx._pool(rel, company)
    if isinstance(f, models.OneToOneField):
        used = set(type(obj)._base_manager.values_list(f.attname, flat=True))
        pool = [p for p in pool if p.pk not in used]
    if not pool:
        if _required(f):
            raise Unseedable(f"needs a {rel._meta.verbose_name} first")
        return None
    return ctx.rng.choice(pool)


def build(model, ctx: Ctx, n: int, preset: dict | None = None, company=None):
    """An unsaved instance of ``model`` with every fillable field set. ``preset`` wins over everything else."""
    preset = preset or {}
    spec = spec_for(model)
    custom = spec.fields if spec else {}
    obj = model()
    if company is None and _company_fk(model):
        company = ctx.company()
    for f in _fields_to_fill(model):
        if f.name in preset:
            setattr(obj, f.name, preset[f.name])
            continue
        if f.name in custom:
            val = custom[f.name]
            setattr(obj, f.name, val(ctx, n) if callable(val) else val)
            continue
        if not f.editable and f.name != "company":
            continue
        if f.is_relation:
            setattr(obj, f.name, _relation_value(f, ctx, obj, company))
            continue
        if not _required(f) and ctx.rng.random() < 0.3:
            continue                                            # leave some optional fields empty, like real data
        val = values.value_for(f, ctx.rng, model, n)
        if val is NotImplemented:
            if _required(f):
                raise Unseedable(f"cannot fake the required field {f.name!r} ({type(f).__name__})")
            continue
        setattr(obj, f.name, val)
    return obj


def _save(model, ctx: Ctx, preset=None, company=None):
    """Build, validate and save one row (retrying with fresh values on unique/validation clashes)."""
    spec = spec_for(model)
    last = None
    for _ in range(TRIES):
        n = ctx.serial(model)
        try:
            with transaction.atomic():
                if spec and spec.factory:
                    obj = spec.factory(ctx, n)
                else:
                    obj = build(model, ctx, n, preset, company)
                    obj.full_clean(exclude=[f.name for f in model._meta.concrete_fields if f.name in SYSTEM_FIELDS])
                    obj.save()
                    _fill_m2m(obj, ctx)
                ctx.remember(obj)
                return obj
        except Unseedable:
            raise
        except (ValidationError, IntegrityError) as exc:
            last = exc
    raise Unseedable(_short(last))


def _short(exc) -> str:
    if isinstance(exc, ValidationError) and hasattr(exc, "message_dict"):
        return "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())[:300]
    return str(exc)[:300]


def _fill_m2m(obj, ctx: Ctx) -> None:
    Company = _company_model()
    company = getattr(obj, "company", None) if _company_fk(type(obj)) else None
    for f in obj._meta.many_to_many:
        if not f.remote_field.through._meta.auto_created:
            continue
        rel = f.related_model
        if rel is Company:
            getattr(obj, f.name).set([ctx.company()] if ctx.companies else [])
            continue
        if rel._meta.app_label == "auth" or (rel._meta.app_label == "core" and rel._meta.model_name in ("role", "group")):
            continue                                            # permissions/roles are never handed out at random
        pool = ctx._pool(rel, company) if rel is not get_user_model() else ctx.users
        if pool:
            getattr(obj, f.name).set(ctx.rng.sample(pool, min(len(pool), ctx.rng.randint(0 if f.blank else 1, 3))))


def _parent_fk(child, parent_model, fk_name):
    if fk_name:
        return child._meta.get_field(fk_name)
    for f in child._meta.concrete_fields:
        if f.is_relation and parent_model._meta.concrete_model in (f.related_model, getattr(f.related_model._meta, "concrete_model", None)):
            return f
    raise Unseedable(f"{child.__name__} has no FK to {parent_model.__name__}")


def _inlines(obj, inlines, ctx: Ctx, result: Result) -> None:
    for inline in inlines:
        preset_base: dict = {}
        if inline.kind == "generic":
            preset_base = {inline.ct_field: ContentType.objects.get_for_model(obj), inline.fk_field: obj.pk}
        else:
            preset_base = {_parent_fk(inline.model, type(obj), inline.fk_name).name: obj}
        company = getattr(obj, "company", None)
        for i in range(ctx.rng.randint(max(1, inline.min_num), 4)):
            preset = dict(preset_base)
            if inline.position_field:
                preset[inline.position_field] = i
            if _company_fk(inline.model) and company is not None:
                preset["company"] = company
            try:
                child = _save(inline.model, ctx, preset, company)
            except Unseedable as exc:
                result.skipped.setdefault(label(inline.model), str(exc))
                break
            result.add(label(inline.model))
            _inlines(child, inline.nested, ctx, result)


def seed_model(model, ctx: Ctx, count: int) -> int:
    """``count`` rows of ``model`` (plus its inline children). Returns the number of parent rows made."""
    from core.crud.registry import resource_for_model

    cfg = resource_for_model(model)
    spec = spec_for(model)
    scoped = _company_fk(model) is not None and bool(ctx.companies)
    made = 0
    for i in range(count):
        # companies take turns; when one lacks a required parent row (no buyer there yet…) the next one is tried
        turn = [ctx.companies[(i + k) % len(ctx.companies)] for k in range(len(ctx.companies))] if scoped else [None]
        obj, problem = None, ""
        for company in turn:
            with acting_as(ctx.user(company), company_ids=[company.pk] if company else None, active_company=company):
                try:
                    obj = _save(model, ctx, company=company)
                except Unseedable as exc:
                    problem = str(exc)
                    continue
                if cfg is not None:
                    _inlines(obj, cfg.inlines, ctx, ctx.result)
                if spec and spec.after:
                    spec.after(ctx, obj)
            break
        if obj is None:
            ctx.result.skipped[label(model)] = problem
            break
        made += 1
        ctx.result.add(label(model))
    return made


# --------------------------------------------------------------------------------------------------------------- people
def _ensure_companies(ctx: Ctx, extra: int) -> None:
    Company = _company_model()
    ctx.companies = list(Company.objects.filter(is_active=True).order_by("pk"))
    if not ctx.companies and not extra:
        extra = 1
    if extra:
        seed_model(Company, ctx, extra)
        ctx.companies = list(Company.objects.filter(is_active=True).order_by("pk"))


def _role(models_seeded) -> object:
    from django.contrib.auth.models import Permission

    from core.models import Role

    role, _ = Role.objects.get_or_create(name=ROLE_NAME)
    business = [m for m in models_seeded if m._meta.app_label != "core"]
    cts = [ContentType.objects.get_for_model(m) for m in business]
    perms = list(Permission.objects.filter(content_type__in=cts))
    perms += list(Permission.objects.filter(content_type__app_label="core",
                                            codename__in=["view_company", "view_department", "view_designation"]))
    role.permissions.add(*perms)
    return role


def seed_users(ctx: Ctx, count: int, models_seeded) -> None:
    from core.models import Department, Designation, ObjectAccess
    from core.permissions.access import access_sources

    User = get_user_model()
    role = _role(models_seeded) if count else None
    for _ in range(count):
        n = ctx.serial(User)
        first, last = values.person(ctx.rng)
        email = f"{first.lower()}.{last.lower()}{n}@example.com"
        if User.objects.filter(email=email).exists():
            continue
        member_of = ctx.rng.sample(ctx.companies, min(len(ctx.companies), ctx.rng.randint(1, 2)))
        home = member_of[0]
        user = User.objects.create_user(email, PASSWORD, full_name=f"{first} {last}")
        profile = user.profile
        profile.companies.set(member_of)
        profile.default_company = profile.active_company = home
        profile.dept = ctx.pick(Department) or profile.dept             # ctx.rng, not order_by("?"): a seed replays the run
        profile.designation = ctx.pick(Designation) or profile.designation
        profile.contact_no, profile.official_id = values.phone(ctx.rng), f"EMP-{n:05d}"
        profile.joining_date = timezone.localdate() - dt.timedelta(days=ctx.rng.randint(30, 2000))
        profile.save()
        user.groups.add(role)
        for source in access_sources():
            ObjectAccess.objects.create(user=user, content_type=ContentType.objects.get_for_model(source, for_concrete_model=False))
        ctx.remember(user)
        ctx.users.append(user)
        ctx.result.add(label(User))
    from core.permissions import cache

    cache.bump()


# --------------------------------------------------------------------------------------------------------------- activity
def _activity(ctx: Ctx, objs) -> None:
    from core import approvals
    from core.approvals import ApprovalError
    from core.mail.service import MailError, send
    from core.models import ApprovalRequest

    submitted = decided = 0
    for obj in objs:
        if ctx.rng.random() > 0.6 or not approvals.has_approval(obj):
            continue
        submitters = [u for u in ctx.users if approvals.can_submit(u, obj)]
        if not submitters:
            continue
        try:
            with transaction.atomic():
                req = approvals.submit_for_approval(ctx.rng.choice(submitters), obj, comment="Seeded request")
            ctx.remember(req)
            submitted += 1
            while req.status == ApprovalRequest.PENDING and ctx.rng.random() < 0.6:
                deciders = [u for u in ctx.users if approvals.can_act(u, req)]
                if not deciders:
                    break
                decision = "reject" if ctx.rng.random() < 0.1 else "approve"
                with transaction.atomic():
                    approvals.submit_approval_step(ctx.rng.choice(deciders), req.pk, decision, comment=values.sentence(ctx.rng, 4))
                req.refresh_from_db()
                decided += 1
        except ApprovalError as exc:
            log.info("seed approval skipped for %s: %s", obj, exc)
    mails = 0
    for _ in range(min(ctx.count, 10) if len(ctx.users) > 1 else 0):
        sender, to = ctx.rng.sample(ctx.users, 2)
        try:
            ctx.remember(send(sender, to=[to], subject=values.sentence(ctx.rng, 4).rstrip("."), body=values.paragraph(ctx.rng)))
            mails += 1
        except MailError:
            continue
    ctx.result.activity.update({"approval requests": submitted, "approval decisions": decided, "mail messages": mails})


# --------------------------------------------------------------------------------------------------------------- entry points
def run(*, models_: list | None = None, count: int = 10, companies: int = 0, users: int = 5, activity: bool = True,
        seed: int | None = None, actor=None) -> Result:
    """Seed ``models_`` (default: every seedable model), ``count`` rows each (Department/Designation too).

    ``seed`` makes the run repeatable: the same seed on the same starting database gives the same rows."""
    from core.models import Department, Designation, Notification

    batch = uuid.uuid4().hex[:12]
    result = Result(batch)
    ctx = Ctx(rng=random.Random(seed), batch=batch, count=count, result=result)
    chosen = [m for m in (models_ or seedable_models())]
    Company = _company_model()
    User = get_user_model()
    first_note = Notification.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    with acting_as(actor):
        _ensure_companies(ctx, companies)
        for org in (Department, Designation):
            if org in chosen:
                seed_model(org, ctx, count)                     # shared by every company
        ctx.users = [u for u in User.objects.filter(is_active=True, is_superuser=False, groups__name=ROLE_NAME).distinct()]
        business = [m for m in chosen if m not in (Company, User, Department, Designation)]
        if User in chosen:
            seed_users(ctx, users, business)
    if not ctx.users:
        ctx.users = list(User.objects.filter(is_active=True).order_by("pk")[:20])
    made_objs: list = []
    for model in ordered(business):
        last = SeedRecord.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        seed_model(model, ctx, (spec_for(model).count if spec_for(model) and spec_for(model).count else count))
        if activity:
            ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
            ids = SeedRecord.objects.filter(batch=batch, content_type=ct, pk__gt=last).values_list("object_id", flat=True)
            made_objs += list(model._base_manager.filter(pk__in=list(ids)))
    if activity:
        _activity(ctx, made_objs)
        for note in Notification.objects.filter(pk__gt=first_note):
            ctx.remember(note)
    return result


def batches() -> list[dict]:
    from django.db.models import Count, Min

    return list(SeedRecord.objects.values("batch").annotate(rows=Count("pk"), at=Min("created_at")).order_by("-at"))


def purge(batch: str | None = None) -> tuple[int, list[str]]:
    """Delete the rows the seeder made (one batch, or all), newest first. Returns (deleted, problems)."""
    from core.models.base import SoftDeleteModel

    records = SeedRecord.objects.select_related("content_type").order_by("-pk")
    if batch:
        records = records.filter(batch=batch)
    pending = list(records)
    deleted, problems = 0, []
    for _ in range(3):                                          # protected rows may free up once their children are gone
        left = []
        for rec in pending:
            model = rec.content_type.model_class()
            obj = model._base_manager.filter(pk=rec.object_id).first() if model is not None else None
            if obj is None:
                rec.delete()
                continue
            try:
                with transaction.atomic():
                    obj.hard_delete() if isinstance(obj, SoftDeleteModel) else obj.delete()
                    rec.delete()
                deleted += 1
            except (ProtectedError, RestrictedError, IntegrityError, PermissionError) as exc:
                left.append((rec, exc))
        pending = [rec for rec, _ in left]
        if not left:
            break
    problems = [f"{rec}: {str(exc)[:120]}" for rec, exc in left] if pending else []
    from core.permissions import cache

    cache.bump()
    return deleted, problems
