"""Which companies a report, query or export covers ("report company scope").

* The user's setting lives on ``Profile``: ``report_all_companies`` (off = the active company only, exactly as before),
  ``report_company_scope`` (active | all | chosen) and ``report_companies`` (the chosen ones).
* A run may pick another mode (``company_scope`` / ``companies`` in the query string or form) and a saved report may carry its own
  default; both only apply while the user's checkbox is on, and both are re-validated here on every run.
* :func:`resolve_report_companies` is the one place the set is computed: *active* = the active company; *all* = every company the
  user belongs to (every active company for a superuser); *chosen* = the chosen ones intersected with that set, so a company the user
  left is dropped at run time. Empty = no rows (fail closed).
* :func:`report_ctx` builds the ``Ctx`` every report path uses; ``company_id`` stays the active company.
* With more than one company in scope the result gains a "Company" column (abbreviation) through the source's ``company_lookup``."""
from __future__ import annotations

from dataclasses import dataclass

from core.crud.access import Ctx
from core.permissions import active_company_id, user_company_ids

ACTIVE, ALL, CHOSEN = "active", "all", "chosen"
MODES = (ACTIVE, ALL, CHOSEN)
MODE_LABELS = {ACTIVE: "Active company", ALL: "All my companies", CHOSEN: "Chosen companies"}
COMPANY_ALIAS, COMPANY_LABEL = "core_company", "Company"
PK_ALIAS = "core_pk"


@dataclass(frozen=True)
class ScopeSetting:
    enabled: bool
    mode: str
    chosen: frozenset


def user_setting(user) -> ScopeSetting:
    """The user's profile setting (checkbox off / no profile = active company only)."""
    from core.models import Profile

    profile = Profile.objects.filter(user_id=getattr(user, "pk", None)).first() if getattr(user, "pk", None) else None
    if profile is None or not profile.report_all_companies:
        return ScopeSetting(False, ACTIVE, frozenset())
    mode = profile.report_company_scope if profile.report_company_scope in MODES else ACTIVE
    chosen = frozenset(profile.report_companies.values_list("pk", flat=True)) if mode == CHOSEN else frozenset()
    return ScopeSetting(True, mode, chosen)


def allowed_company_ids(user) -> frozenset:
    """Companies a report may ever cover for ``user``: their memberships, or every active company for a superuser."""
    if getattr(user, "is_superuser", False) and getattr(user, "is_active", False):
        from core.models import Company

        return frozenset(Company.objects.filter(is_active=True, deleted_at__isnull=True).values_list("pk", flat=True))
    return user_company_ids(user)


def _ids(values) -> frozenset:
    out = set()
    for v in values or ():
        try:
            out.add(int(getattr(v, "pk", v)))
        except (TypeError, ValueError):
            continue
    return frozenset(out)


def effective_mode(user, mode=None, chosen_ids=None, setting: ScopeSetting | None = None) -> tuple[str, frozenset]:
    """``(mode, chosen ids)`` after applying the user's setting: checkbox off forces *active*; no / unknown mode = the setting's."""
    setting = setting or user_setting(user)
    if not setting.enabled:
        return ACTIVE, frozenset()
    if mode not in MODES:
        mode, chosen_ids = setting.mode, setting.chosen
    elif mode == CHOSEN and chosen_ids is None:
        chosen_ids = setting.chosen
    return mode, _ids(chosen_ids) if mode == CHOSEN else frozenset()


def resolve_report_companies(user, mode=None, chosen_ids=None) -> frozenset:
    """Company ids a report run by ``user`` covers. ``mode`` None = the user's setting. Never wider than :func:`allowed_company_ids`."""
    mode, chosen = effective_mode(user, mode, chosen_ids)
    if mode == ACTIVE:
        active = active_company_id(user)
        return frozenset({active}) if active else frozenset()
    allowed = allowed_company_ids(user)
    return allowed if mode == ALL else chosen & allowed


# ------------------------------------------------------------------------------------------------------------- request / ctx
def choice_from(data, saved=None) -> tuple[str | None, frozenset | None]:
    """The run's choice from a QueryDict (``company_scope`` + ``companies``), else the saved report's default, else ``(None, None)``."""
    mode = (data.get("company_scope") or "") if data is not None else ""
    if mode in MODES:
        return mode, _ids(data.getlist("companies")) if mode == CHOSEN else None
    if saved is not None and getattr(saved, "company_scope", "") in MODES:
        return saved.company_scope, _ids(saved.scope_companies.values_list("pk", flat=True)) if saved.company_scope == CHOSEN else None
    return None, None


def build_ctx(user, active_id, mode=None, chosen_ids=None) -> Ctx:
    """Report ``Ctx`` for ``user``: data scope = the resolved companies, ``company_id`` = the active company (letterhead, jobs)."""
    members = user_company_ids(user)
    mode, chosen = effective_mode(user, mode, chosen_ids)
    active = active_id if active_id in members else None
    if mode == ACTIVE:
        ids = frozenset({active}) if active else frozenset()
    else:
        allowed = allowed_company_ids(user)
        ids = allowed if mode == ALL else chosen & allowed
    return Ctx(user=user, company_ids=ids, company_id=active, member_ids=members, report_scope=mode)


def report_ctx(request, saved=None) -> Ctx:
    data = request.POST if request.method == "POST" and "company_scope" in request.POST else request.GET
    mode, chosen = choice_from(data, saved)
    return build_ctx(request.user, active_company_id(request.user), mode, chosen)


def scope_params(ctx: Ctx) -> dict:
    """What a background export job stores to reproduce the run (re-resolved against the owner when it runs)."""
    mode = ctx.report_scope or ACTIVE
    return {"company_scope": mode, "companies": sorted(ctx.company_ids) if mode == CHOSEN else []}


def ctx_from_params(user, active_id, params: dict) -> Ctx:
    mode = params.get("company_scope") if isinstance(params, dict) else None
    return build_ctx(user, active_id, mode if mode in MODES else ACTIVE, params.get("companies") or [] if mode == CHOSEN else None)


def query_string(ctx: Ctx) -> str:
    """``company_scope=...&companies=...`` for links that must reproduce the run (exports)."""
    from urllib.parse import urlencode

    params = scope_params(ctx)
    return urlencode([("company_scope", params["company_scope"])] + [("companies", i) for i in params["companies"]])


def remember_scope(saved, request, ctx: Ctx) -> None:
    """Saved report: keep the run's company choice as its default when "Remember the company choice" is ticked, else clear it
    (= the user's own setting). Chosen companies are stored as resolved, so they never exceed what the saver may see."""
    keep = request.POST.get("keep_scope") and user_setting(request.user).enabled and ctx.report_scope in MODES
    saved.company_scope = ctx.report_scope if keep else ""
    saved.save(update_fields=["company_scope", "updated_at"])
    saved.scope_companies.set(sorted(ctx.company_ids) if keep and ctx.report_scope == CHOSEN else [])


def scope_ui(user, ctx: Ctx) -> dict:
    """Context for ``core/reports/_company_scope.html`` (hidden when the user's checkbox is off)."""
    from core.models import Company

    setting = user_setting(user)
    if not setting.enabled:
        return {"enabled": False}
    companies = Company.objects.filter(pk__in=allowed_company_ids(user)).order_by("name")
    mode = ctx.report_scope or setting.mode
    return {"enabled": True, "mode": mode, "modes": list(MODE_LABELS.items()), "companies": companies,
            "chosen": set(ctx.company_ids) if mode == CHOSEN else set(), "query": query_string(ctx)}


# ------------------------------------------------------------------------------------------------------------- company column
def company_labels(ids) -> dict:
    """``{id: (abbreviation, name)}`` (deleted companies included, so old rows still read)."""
    from core.models import Company

    return {c.pk: (c.abbreviation or c.name, c.name) for c in Company.global_objects.filter(pk__in=list(ids)).only("pk", "abbreviation", "name")}


def wants_company_column(ctx: Ctx, lookup: str | None) -> bool:
    return bool(lookup) and len(ctx.company_ids or ()) > 1


def single_valued(model, lookup: str) -> bool:
    """True when ``lookup`` reaches at most one company per row (a FK path, or the company itself)."""
    if lookup in ("id", "pk"):
        return True
    current = model
    for part in lookup.split("__"):
        try:
            field = current._meta.get_field(part)
        except Exception:  # noqa: BLE001 - unknown path: treat as many (Python fill-in, never a broken query)
            return False
        if field.many_to_many or field.one_to_many:
            return False
        current = field.related_model
        if current is None:
            return False
    return True


def abbreviation_path(lookup: str) -> str:
    return "abbreviation" if lookup in ("id", "pk") else f"{lookup}__abbreviation"


def row_companies(model, lookup: str, pks, ctx: Ctx) -> dict:
    """``{row pk: "A, B"}``: the abbreviations of the row's companies that are in scope (one query + one for the names)."""
    path = "pk" if lookup in ("id", "pk") else lookup
    pairs = [(pk, cid) for pk, cid in model._base_manager.filter(pk__in=pks).values_list("pk", path) if cid in ctx.company_ids]
    labels = company_labels({cid for _pk, cid in pairs})
    out: dict = {}
    for pk, cid in sorted(pairs, key=lambda p: labels.get(p[1], ("",))[0]):
        out.setdefault(pk, []).append(labels.get(cid, (str(cid),))[0])
    return {pk: ", ".join(names) for pk, names in out.items()}


def company_names(ctx: Ctx) -> dict:
    """``{abbreviation: full name}`` for the tooltip on the Company column."""
    return {abbr: name for abbr, name in company_labels(ctx.company_ids).values()}


def audit_note(ctx: Ctx) -> str:
    """``"companies: All my companies (A, B)"`` for report_run / export audit rows (never row data)."""
    abbrs = sorted(abbr for abbr, _name in company_labels(ctx.company_ids or ()).values())
    return f"companies: {MODE_LABELS.get(ctx.report_scope or ACTIVE, ctx.report_scope)} ({', '.join(abbrs) or 'none'})"

