"""Saved reports in the sidebar menu: which menu groups a report may be placed under, and the server-side check of the Save form.

* A report (``ReportDefinition`` / ``QueryReport``) is shown under ``menu_parent``, a menu **group** (a ``MenuItem`` with no URL
  name and no path), as ``menu_label`` (default: its name), ordered by ``menu_position`` then label. The entries themselves are built
  by ``core.menu.service._reports_in_menu`` for each user (only reports they may open, from sources they may use).
* A group can be picked when it and every group above it are active, the user can see them (superuser-only / permission) and there
  is room for one more level (``CORE_MENU_MAX_DEPTH``).
* Custom reports are placed from code: ``register_custom_report(..., menu="Inventory > Yarn")`` names the group by its titles.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import conf

POSITION_MAX = 32767


class MenuPlacementError(ValueError):
    pass


@dataclass(frozen=True)
class MenuGroup:
    pk: int
    path: str                       # "Inventory > Yarn"
    depth: int                      # 1 = top level

    @property
    def indent(self) -> str:
        return "   " * (self.depth - 1)


def is_group(item) -> bool:
    return not item.url_name and not item.path


def _items(items=None) -> list:
    from core.models import MenuItem

    return list(MenuItem.objects.all()) if items is None else list(items)


def walk(items=None) -> list[tuple]:
    """``[(item, depth, chain)]`` for every item whose own row and all ancestors are active, in menu order (``chain`` = the items
    from the top level down to it). Never loops on corrupt data; stops at ``CORE_MENU_MAX_DEPTH``."""
    by_parent: dict = {}
    for it in sorted(_items(items), key=lambda i: (i.position, i.pk)):
        by_parent.setdefault(it.parent_id, []).append(it)
    max_depth, seen, out = conf.get("CORE_MENU_MAX_DEPTH"), set(), []

    def visit(parent_id, depth, chain):
        if depth > max_depth:
            return
        for it in by_parent.get(parent_id, []):
            if not it.is_active or it.pk in seen:
                continue
            seen.add(it.pk)
            out.append((it, depth, (*chain, it)))
            visit(it.pk, depth + 1, (*chain, it))

    visit(None, 1, ())
    return out


def _sees(user, item) -> bool:
    from core.menu.service import _allowed

    return (not item.superuser_only or user.is_superuser) and _allowed(user, item.permission)


def _title_path(chain) -> str:
    return " > ".join(i.title for i in chain)


def group_choices(user, *, keep=None, items=None) -> list[MenuGroup]:
    """Groups ``user`` may place a report under, in menu order. ``keep``: the report's current group, offered even when the user
    cannot see it (so re-saving does not drop it) as long as it is still an active group with room below it."""
    max_depth = conf.get("CORE_MENU_MAX_DEPTH")
    out = []
    for it, depth, chain in walk(items):
        if not is_group(it) or depth >= max_depth:
            continue
        if it.pk == keep or all(_sees(user, i) for i in chain):
            out.append(MenuGroup(it.pk, _title_path(chain), depth))
    return out


def group_paths(items=None) -> dict[int, str]:
    """``{group pk: "Inventory > Yarn"}`` for the active groups (for labels on the Reports page)."""
    return {it.pk: _title_path(chain) for it, _depth, chain in walk(items) if is_group(it)}


def _key(path: str) -> tuple:
    return tuple(part.strip().casefold() for part in str(path).split(">") if part.strip())


def find_group(path: str, items=None):
    """pk of the active group whose titles from the top level are ``path`` ("Inventory > Yarn", case-insensitive), or None."""
    wanted = _key(path)
    if not wanted:
        return None
    max_depth = conf.get("CORE_MENU_MAX_DEPTH")
    for it, depth, chain in walk(items):
        if is_group(it) and depth < max_depth and tuple(i.title.strip().casefold() for i in chain) == wanted:
            return it.pk
    return None


def read_placement(user, data, *, current=None) -> dict | None:
    """The menu fields posted with a Save form, checked on the server; None when the form did not carry them (nothing changes).
    Raises ``MenuPlacementError`` for a group that is unknown, inactive, a link rather than a group, hidden from the user or too
    deep, and for a position that is not a whole number."""
    if "menu_parent" not in data:
        return None
    raw = str(data.get("menu_parent") or "").strip()
    label = str(data.get("menu_label") or "").strip()[:80]
    position = str(data.get("menu_position") or "").strip() or "100"
    if not position.isdigit() or int(position) > POSITION_MAX:
        raise MenuPlacementError("The menu position must be a whole number from 0 to 32767.")
    if not raw:
        return {"menu_parent_id": None, "menu_label": label, "menu_position": int(position)}
    allowed = {g.pk for g in group_choices(user, keep=current)}
    if not raw.isdigit() or int(raw) not in allowed:
        raise MenuPlacementError("Choose an active menu group you can see (a group, not a link) to show the report under.")
    return {"menu_parent_id": int(raw), "menu_label": label, "menu_position": int(position)}


def apply_placement(report, placement: dict | None) -> None:
    """Copy a checked placement onto the report (before ``save()``)."""
    for name, value in (placement or {}).items():
        setattr(report, name, value)
