"""SavedSearch service: visibility, validation on save AND load, duplicate/clone/share/default/pin, audit.

Who may build: superusers and users holding ``core.build_search``. Everyone else only *uses* the searches offered to them
(their company's shared searches and the administrator's standard searches, ``is_system``)."""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.text import slugify

from core.audit.service import audited_update, record_event
from core.models import SavedSearch
from core.permissions import user_company_ids, user_has_perm
from core.search.builder.state import SearchState, clamp_page_size, clean_sort
from core.search.builder.tree import compile_tree, validate_tree
from core.search.errors import SearchError
from core.search.registry import get_search

log = logging.getLogger("core.search")


def _audit(action: str, saved: SavedSearch) -> None:
    record_event("saved_search", obj=saved, message=action, company=saved.company_id)


BUILD_PERMISSION = "core.build_search"


def can_build(user) -> bool:
    """May ``user`` compose advanced filters and save searches? (Quick search and offered searches are open to everyone.)"""
    return bool(getattr(user, "is_authenticated", False) and (user.is_superuser or user_has_perm(user, BUILD_PERMISSION)))


def _require_builder(user) -> None:
    if not can_build(user):
        raise SearchError("You can use the saved searches offered to you, but not build or change searches.")


def _require_system_rights(user, saved: SavedSearch | None, is_system: bool) -> None:
    if (is_system or (saved is not None and saved.is_system)) and not getattr(user, "is_superuser", False):
        raise SearchError("Only an administrator can manage standard searches.")


def visible_to(user, target_type: str, key: str, company_id=None):
    """Saved searches ``user`` may see for a target: their own and shared ones of the company (never across companies), plus the
    administrator's standard searches (any company -- the rows they return are still company scoped). Anonymous -> none."""
    members = user_company_ids(user)
    if company_id is not None:
        members = members & {company_id}
    if not members:
        return SavedSearch.objects.none()
    own_company = Q(company_id__in=members) & (Q(owner=user) | Q(visibility__in=["company", "public"]))
    return (SavedSearch.objects.filter(target_type=target_type, target_key=key).filter(own_company | Q(is_system=True))
            .select_related("owner"))


def validated_state(saved: SavedSearch) -> SearchState | None:
    """Re-validate against the registry; return None (and log) when the definition is no longer valid -- never raises."""
    try:
        spec = get_search(saved.target_type, saved.target_key)
        tree = validate_tree(spec, saved.definition)
        compile_tree(spec, tree)
        return SearchState(quick=saved.quick or "", tree=tree, sort=clean_sort(spec, saved.sort or []),
                           page_size=clamp_page_size(saved.page_size), saved_id=saved.pk)
    except SearchError as exc:
        log.warning("saved search %s dropped: %s", saved.pk, exc)
        return None


def usable_searches(user, target_type: str, key: str, company_id=None) -> list[SavedSearch]:
    return [s for s in visible_to(user, target_type, key, company_id) if validated_state(s) is not None]


@transaction.atomic
def save_search(user, company_id, *, target_type: str, key: str, name: str, state: SearchState, visibility: str = "private",
                pinned: bool = False, is_default: bool = False, instance: SavedSearch | None = None, is_system: bool = False) -> SavedSearch:
    _require_builder(user)
    _require_system_rights(user, instance, is_system)
    if company_id not in user_company_ids(user):
        raise SearchError("You cannot save searches for that company.")
    if visibility not in ("private", "company", "public"):
        raise SearchError("Unknown visibility.")
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise SearchError("Give the search a name (up to 120 characters).")
    spec = get_search(target_type, key)
    tree = validate_tree(spec, state.tree)
    compile_tree(spec, tree)                                    # fail now, not when someone opens it
    sort = clean_sort(spec, state.sort)
    if instance is not None and instance.owner_id != user.pk and not (instance.is_system and user.is_superuser):
        raise SearchError("Only the owner can change a saved search.")
    saved = instance or SavedSearch(owner=user, company_id=company_id, target_type=target_type, target_key=key)
    saved.name, saved.visibility, saved.pinned_to_menu = name, visibility, pinned
    saved.is_system = bool(is_system)
    saved.quick, saved.definition, saved.sort = state.quick, tree, sort
    saved.page_size = clamp_page_size(state.page_size or None)
    saved.slug = slugify(name)[:140] or "search"
    try:
        with transaction.atomic():
            if is_default:
                audited_update(SavedSearch.objects.filter(owner=user, company_id=saved.company_id, target_type=target_type, target_key=key,
                                                          is_default=True).exclude(pk=saved.pk), message="no longer the default", is_default=False)
            saved.is_default = is_default
            saved.save()
    except IntegrityError:
        raise SearchError("You already have a saved search with that name for this page.") from None
    _audit("saved" if instance is None else "updated", saved)
    return saved


def _owned(user, saved: SavedSearch) -> None:
    """Changing a search needs builder rights; standard searches belong to the administrators, the rest to their owner."""
    _require_builder(user)
    if saved.is_system:
        _require_system_rights(user, saved, True)
    elif saved.owner_id != user.pk:
        raise SearchError("Only the owner can do that.")


def delete_search(user, saved: SavedSearch) -> None:
    _owned(user, saved)
    _audit("deleted", saved)
    saved.delete()


def set_default(user, saved: SavedSearch, value: bool = True) -> SavedSearch:
    if saved.owner_id != user.pk and not visible_to(user, saved.target_type, saved.target_key, saved.company_id).filter(pk=saved.pk).exists():
        raise SearchError("Not available.")
    with transaction.atomic():
        audited_update(SavedSearch.objects.filter(owner=user, company_id=saved.company_id, target_type=saved.target_type,
                                                  target_key=saved.target_key, is_default=True), message="no longer the default", is_default=False)
        if saved.owner_id == user.pk:
            saved.is_default = value
            saved.save(update_fields=["is_default", "updated_at"])
        else:                                                   # a shared search becomes MY default via a private clone
            clone(user, saved, name=saved.name, is_default=value)
    return saved


def share(user, saved: SavedSearch, visibility: str) -> SavedSearch:
    _owned(user, saved)
    if visibility not in ("private", "company", "public"):
        raise SearchError("Unknown visibility.")
    saved.visibility = visibility
    saved.save(update_fields=["visibility", "updated_at"])
    _audit(f"visibility={visibility}", saved)
    return saved


def pin(user, saved: SavedSearch, pinned: bool) -> SavedSearch:
    _owned(user, saved)
    saved.pinned_to_menu = pinned
    saved.save(update_fields=["pinned_to_menu", "updated_at"])
    from core.menu.service import invalidate_menus

    invalidate_menus()
    _audit("pinned" if pinned else "unpinned", saved)
    return saved


def clone(user, saved: SavedSearch, name: str | None = None, is_default: bool = False) -> SavedSearch:
    """Copy a search the user can see into their own private search (validated again through ``save_search``)."""
    _require_builder(user)
    if not visible_to(user, saved.target_type, saved.target_key, saved.company_id).filter(pk=saved.pk).exists():
        raise SearchError("Not available.")
    state = validated_state(saved)
    if state is None:
        raise SearchError("That saved search is no longer valid.")
    base = (name or f"{saved.name} (copy)")[:112]
    candidate, n = base, 1
    while SavedSearch.objects.filter(owner=user, company_id=saved.company_id, target_type=saved.target_type,
                                     target_key=saved.target_key, slug=slugify(candidate)).exists():
        n += 1
        candidate = f"{base} {n}"
    return save_search(user, saved.company_id, target_type=saved.target_type, key=saved.target_key, name=candidate, state=state,
                       visibility="private", is_default=is_default)


duplicate = clone
