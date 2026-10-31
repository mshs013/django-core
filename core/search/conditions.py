"""Record conditions ("only when") shared by approval steps and notification rules.

A condition is the search builder's filter tree, so it can use exactly the filterable fields (and operators) of the model's
list page and nothing else. Shorthands accepted from admin forms: one row ``{"field": ..., "op": ..., "value": ...}`` or a list of
rows (all must match). Errors are :class:`SearchError` with a readable message.
"""
from __future__ import annotations

from core.search.errors import SearchError

EMPTY = {"op": "AND", "items": []}


def normalize(raw) -> dict:
    if not raw:
        return dict(EMPTY)
    if isinstance(raw, list):
        return {"op": "AND", "items": raw}
    if isinstance(raw, dict) and "items" in raw:
        return raw
    if isinstance(raw, dict) and "field" in raw:
        return {"op": "AND", "items": [raw]}
    raise SearchError("A condition must be a filter row, a list of rows or a filter group.")


def spec_for(model):
    from core.crud.registry import resource_for_model
    from core.search.registry import get_search

    cfg = resource_for_model(model)
    if cfg is None:
        raise SearchError(f"{model._meta.verbose_name.capitalize()} has no list page, so conditions cannot be used for it.")
    return get_search("resource", cfg.slug)


def validate(model, raw) -> dict:
    """Normalised, validated tree (empty tree = always)."""
    tree = normalize(raw)
    if not tree.get("items"):
        return dict(EMPTY)
    if model is None:
        raise SearchError("Choose the records this applies to before adding a condition.")
    from core.search.builder.tree import compile_tree, validate_tree

    spec = spec_for(model)
    tree = validate_tree(spec, tree)
    compile_tree(spec, tree)
    return tree


def matches(obj, tree: dict) -> bool:
    """Does the saved row ``obj`` match ``tree``? (Evaluated in the database; deleted-but-present rows included.)"""
    if not tree or not tree.get("items"):
        return True
    from core.search.builder.tree import compile_tree

    model = type(obj)
    return model._base_manager.filter(pk=obj.pk).filter(compile_tree(spec_for(model), tree)).exists()
