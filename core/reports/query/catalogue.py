"""Which fields a user can reach from a root model: every allowed field of the root and of the models joined through
ForeignKey / OneToOne / ManyToMany relations (both directions), up to ``CORE_QUERY_MAX_DEPTH`` joins. The same walk validates
every path in a saved or submitted definition, so a path that is not in the catalogue can never reach the ORM."""
from __future__ import annotations

from dataclasses import dataclass

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.db import models

from core import conf

from .registry import QueryModel, allowed_for, query_model_for

SEP = " › "


class QueryError(Exception):
    """A user-safe refusal (the message can be shown as-is)."""


@dataclass(frozen=True)
class Hop:
    prefix: str                     # ORM path up to and including this relation, e.g. "employee__official_info"
    entry: QueryModel               # the model reached
    many: bool                      # crosses a to-many relation


@dataclass(frozen=True)
class FieldRef:
    path: str
    field: models.Field
    kind: str
    label: str
    hops: tuple[Hop, ...]

    @property
    def many(self) -> bool:
        return any(h.many for h in self.hops)


def kind_of(f) -> str | None:
    """Value kind used for operators, functions and inputs; ``None`` = not offered."""
    if f.is_relation:
        return "fk" if (f.many_to_one or f.one_to_one) and f.concrete else None
    if getattr(f, "choices", None):
        return "choice"
    if isinstance(f, models.BooleanField):
        return "bool"
    if isinstance(f, models.DateTimeField):
        return "datetime"
    if isinstance(f, models.DateField):
        return "date"
    if isinstance(f, models.TimeField):
        return "time"
    if isinstance(f, (models.IntegerField, models.DecimalField, models.FloatField, models.AutoField)):
        return "number"
    if isinstance(f, models.UUIDField):
        return "uuid"
    if isinstance(f, (models.CharField, models.TextField, models.GenericIPAddressField)):
        return "text"
    return None


def _label(f) -> str:
    if f.is_relation and not f.concrete:                   # reverse relation: the other model's plural name
        text = str(f.related_model._meta.verbose_name_plural)
    else:
        text = str(getattr(f, "verbose_name", f.name))
    return text[:1].upper() + text[1:]


def _relations(entry: QueryModel, user):
    """(field, target entry, many) for every relation of ``entry`` that leads to a joinable model the user may view."""
    for f in entry.model._meta.get_fields():
        if not f.is_relation or isinstance(f, (GenericForeignKey, GenericRelation)) or f.related_model is None:
            continue
        if f.concrete and not entry.field_allowed(f):
            continue
        target = query_model_for(f.related_model)
        if target is None or not target.joinable or not allowed_for(user, target):
            continue
        yield f, target, bool(f.many_to_many or f.one_to_many)


def _value_fields(entry: QueryModel):
    for f in entry.model._meta.concrete_fields:
        if not entry.field_allowed(f):
            continue
        kind = kind_of(f)
        if kind is not None:
            yield f, kind


def max_depth() -> int:
    return max(0, min(int(conf.get("CORE_QUERY_MAX_DEPTH")), 5))


def catalogue(root: QueryModel, user) -> list[dict]:
    """Flat list for the builder UI: ``{"p": path, "l": label, "g": group, "k": kind, "c": choices, "m": crosses to-many,
    "j": the joined table's path ("" for the root table)}``."""
    out: list[dict] = []
    limit = conf.get("CORE_QUERY_MAX_FIELDS")
    queue = [("", root.label, root, False, 0, None)]
    while queue and len(out) < limit:
        prefix, group, entry, many, depth, came_from = queue.pop(0)
        for f, kind in _value_fields(entry):
            path = f"{prefix}{f.name}"
            label = _label(f)
            out.append({"p": path, "l": label if not prefix else f"{group}{SEP}{label}", "g": group, "k": kind, "m": many,
                        "j": prefix[:-2],
                        "c": [[str(v), str(t)] for v, t in f.flatchoices] if kind == "choice" else []})
            if len(out) >= limit:
                break
        if depth >= max_depth():
            continue
        for f, target, to_many in _relations(entry, user):
            if came_from is not None and f.remote_field is came_from:
                continue                                        # do not walk straight back over the relation just used
            queue.append((f"{prefix}{f.name}__", f"{group}{SEP}{_label(f)}", target, many or to_many, depth + 1, f))
    return out


def tables(fields: list[dict]) -> list[dict]:
    """The tables that can be joined, from a :func:`catalogue`: ``{"p": path, "l": label, "m": to-many}`` in catalogue order."""
    seen, out = set(), []
    for f in fields:
        if f["j"] and f["j"] not in seen:
            seen.add(f["j"])
            out.append({"p": f["j"], "l": f["g"], "m": f["m"]})
    return out


def table_of(path: str) -> str:
    """``"lines__qty"`` -> ``"lines"``; a root field -> ``""``."""
    return path.rpartition("__")[0]


def resolve(root: QueryModel, user, path: str) -> FieldRef:
    """Validate one path against the same rules as :func:`catalogue`; raises :class:`QueryError`."""
    if not isinstance(path, str) or not path or len(path) > 300:
        raise QueryError("Choose a field.")
    parts = path.split("__")
    if len(parts) - 1 > max_depth():
        raise QueryError(f"{path[:60]!r} joins too many tables.")
    entry, hops, labels, came_from = root, [], [], None
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if not last:
            match = next(((f, t, m) for f, t, m in _relations(entry, user) if f.name == part and f.remote_field is not came_from), None)
            if match is None:
                raise QueryError(f"Unknown or forbidden relation in {path[:60]!r}.")
            f, entry, many = match
            hops.append(Hop("__".join(parts[: index + 1]), entry, many))
            labels.append(_label(f))
            came_from = f
            continue
        f = next((f for f, _k in _value_fields(entry) if f.name == part), None)
        if f is None:
            raise QueryError(f"Unknown or forbidden field {path[:60]!r}.")
        labels.append(_label(f))
        return FieldRef(path, f, kind_of(f), SEP.join(labels), tuple(hops))
    raise QueryError("Choose a field.")
