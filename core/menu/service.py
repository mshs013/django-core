"""Navigation: DB-driven MenuItems (the default menu is seeded as rows, see ``defaults.py``) + saved / custom reports placed under a
menu group + optional code-declared entries + pinned saved searches, filtered by permission, cached per user/company. Hiding an entry
is presentation only -- every target view enforces its own permission."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode

from core import conf
from core.permissions import cache, user_has_perm


@dataclass
class Entry:
    title: str
    url: str
    icon: str = ""
    children: list["Entry"] = field(default_factory=list)
    key: str = ""


_STATIC: list[dict] = []


def register_menu_entry(title: str, url_name: str = "", *, path: str = "", icon: str = "", permission: str = "", parent: str = "",
                        position: int = 100) -> None:
    """Code-declared entry (e.g. from a business app's ``ready()``). ``parent`` is the title of a top-level group (created on demand)."""
    _STATIC.append({"title": title, "url_name": url_name, "path": path, "icon": icon, "permission": permission, "parent": parent,
                    "position": position})


def invalidate_menus() -> None:
    cache.bump()


def _url(url_name: str, path: str) -> str:
    if url_name:
        try:
            return reverse(url_name)
        except NoReverseMatch:
            return ""
    return path if path.startswith("/") and not path.startswith("//") else ""


def _allowed(user, permission: str) -> bool:
    return not permission or user_has_perm(user, permission)


def _db_tree(user, company_id=None) -> list[Entry]:
    from core.models import MenuItem
    from core.reports.menu import is_group

    items = list(MenuItem.objects.filter(is_active=True).order_by("position", "pk"))
    by_parent: dict = {}
    for it in items:
        by_parent.setdefault(it.parent_id, []).append(it)
    reports = _reports_in_menu(user, company_id, items)
    visiting: set = set()
    max_depth = conf.get("CORE_MENU_MAX_DEPTH")

    def build(parent_id, depth=1, extra=()) -> list[Entry]:
        out = []
        if depth > max_depth:                           # defensive: rows saved past the limit are not drawn
            return out
        for it in by_parent.get(parent_id, []):
            if it.pk in visiting:                       # defensive: never loop on corrupt data
                continue
            visiting.add(it.pk)
            kids = build(it.pk, depth + 1, reports.get(it.pk, ()) if is_group(it) else ())
            visiting.discard(it.pk)
            url = _url(it.url_name, it.path)
            if (it.superuser_only and not user.is_superuser) or not _allowed(user, it.permission) or (not url and not kids):
                continue
            out.append(((it.position, 0, "", it.pk), Entry(it.title, url, it.icon, kids, f"db{it.pk}")))
        out.extend(extra)                               # reports placed under this group, merged by position
        return [entry for _key, entry in sorted(out, key=lambda pair: pair[0])]

    return build(None)


def _reports_in_menu(user, company_id=None, items=None) -> dict[int, list]:
    """``{menu group pk: [(sort key, Entry)]}``: saved reports / queries placed under a group (``menu_parent``) that ``user`` may open
    (owner, shared with them or their groups, all users, standard) *and* whose source they may use, plus custom reports registered
    with ``menu="A > B"`` they hold the permission for. Saved reports belong to no company, so ``company_id`` does not filter them.
    Links are reversed from the source key / root and the pk, never built from user text; a missing source drops the entry."""
    from core.crud.access import allowed
    from core.models import QueryReport
    from core.reports import service as report_service
    from core.reports.custom import all_custom_reports
    from core.reports.menu import find_group
    from core.reports.query import allowed_for, get_query_model
    from core.reports.registry import get_report_source

    out: dict[int, list] = {}

    def add(parent_id, position, title, url, icon, key):
        out.setdefault(parent_id, []).append(((position, 1, title.casefold(), key), Entry(title, url, icon, key=key)))

    fields = ("pk", "name", "menu_label", "menu_parent_id", "menu_position")
    for d in report_service.visible_definitions(user, None).filter(menu_parent__isnull=False).only(*fields, "source_key"):
        source = get_report_source(d.source_key)
        if source is None or not all(allowed(user, p) for p in source.permission):
            continue
        try:
            url = reverse("core:report_builder", args=[d.source_key])
        except NoReverseMatch:
            continue
        add(d.menu_parent_id, d.menu_position, d.menu_title, f"{url}?{urlencode({'def': d.pk})}", "report", f"report{d.pk}")
    for q in report_service.visible_definitions(user, None, model=QueryReport).filter(menu_parent__isnull=False).only(*fields, "root"):
        entry = get_query_model(q.root)
        if entry is None or not entry.root or not allowed_for(user, entry):
            continue
        try:
            url = reverse("core:query_builder", args=[q.root])
        except NoReverseMatch:
            continue
        add(q.menu_parent_id, q.menu_position, q.menu_title, f"{url}?{urlencode({'q': q.pk})}", "table", f"query{q.pk}")
    for r in all_custom_reports():
        if not r.menu or not all(allowed(user, p) for p in r.permission):
            continue
        parent = find_group(r.menu, items)
        if parent is None:
            continue
        add(parent, r.menu_position, r.menu_label or r.label, reverse("core:custom_report", args=[r.key]), "file-analytics", f"custom-{r.key}")
    return out


def _registry_entries(user) -> list[Entry]:
    from core.crud.registry import all_resources

    groups: dict[str, Entry] = {}
    entries: list[Entry] = []
    for spec in sorted(_STATIC, key=lambda s: (s["position"], s["title"])):
        if not _allowed(user, spec["permission"]):
            continue
        url = _url(spec["url_name"], spec["path"])
        if not url:
            continue
        entry = Entry(spec["title"], url, spec["icon"], key=f"static:{spec['title']}")
        if spec["parent"]:
            group = groups.setdefault(spec["parent"], Entry(spec["parent"], "", "folder", key=f"group:{spec['parent']}"))
            group.children.append(entry)
        else:
            entries.append(entry)
    if conf.get("CORE_MENU_AUTO_RESOURCES"):               # opt-in convenience for projects that do not curate their menu
        resource_children = []
        for cfg in all_resources():
            if cfg.menu and user_has_perm(user, cfg.perm("view")):
                resource_children.append(Entry(cfg.label_plural, reverse("core:resource_list", args=[cfg.slug]), cfg.icon, key=f"res:{cfg.slug}"))
        if resource_children:
            entries.append(Entry("Data", "", "database", resource_children, "group:data"))
    return [*groups.values(), *entries]


def _pinned(user, company_id) -> list[Entry]:
    from core.models import SavedSearch
    from core.permissions import user_company_ids

    if company_id not in user_company_ids(user):
        return []
    from django.db.models import Q

    from core.crud.access import allowed
    from core.crud.registry import get_resource

    rows = SavedSearch.objects.filter(pinned_to_menu=True, target_type="resource").filter(
        (Q(company_id=company_id) & (Q(owner=user) | Q(visibility__in=["company", "public"]))) | Q(is_system=True))[:20]
    out = []
    for s in rows:
        cfg = get_resource(s.target_key)
        if cfg is None or not allowed(user, cfg.perm("view")):
            continue
        try:
            base = reverse("core:resource_list", args=[s.target_key])
        except NoReverseMatch:
            continue
        out.append(Entry(s.name, f"{base}?saved={s.pk}", "search", key=f"saved{s.pk}"))
    return [Entry("Saved searches", "", "bookmark", out, "group:saved")] if out else []


def menu_for(user, company_id=None) -> list[Entry]:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return []
    ck = ("menu", user.pk, company_id)
    cached = cache.get(*ck)
    if cached is not None:
        return cached
    tree = [*_db_tree(user, company_id), *_registry_entries(user), *_pinned(user, company_id)]
    cache.put(tree, *ck, timeout=conf.get("CORE_MENU_CACHE_SECONDS"))
    return tree


def validate_parent(item_pk, parent_pk) -> bool:
    """False when making ``parent_pk`` the parent of ``item_pk`` would create a cycle."""
    from core.models import MenuItem

    seen = set()
    node = MenuItem.objects.filter(pk=parent_pk).first() if parent_pk else None
    while node is not None and node.pk not in seen:
        if item_pk and node.pk == item_pk:
            return False
        seen.add(node.pk)
        node = node.parent
    return True
