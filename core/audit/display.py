"""Human-readable audit rows: the model's verbose name instead of ``app.Model``; every field of the record with the changed ones
highlighted, as "New" / "Old" bullet lists (rows written before snapshots list only the ``{field: [old, new]}`` changes) -- with field labels,
related objects by name (not id), choice labels and dates in the site format."""
from __future__ import annotations

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.html import format_html, format_html_join

from core.formatting import format_value

from .redaction import MASK

EMPTY = "—"
# headings of many-to-many rows (core.audit.m2m) whose field name alone reads badly
M2M_LABELS = {"groups": "Roles", "user_permissions": "Single permissions", "permissions": "Permissions", "user_set": "Members",
              "companies": "Member of", "report_companies": "Chosen report companies", "approver_steps": "Approves these steps",
              "step_adders": "May add steps", "member_profiles": "Members", "see_users": "Also sees the records of"}


def model_for_label(label: str):
    try:
        return apps.get_model(label) if label and "." in label else None
    except (LookupError, ValueError):
        return None


def verbose_model_name(label: str) -> str:
    """``"core.User"`` -> ``"User"``, ``"core.BackupRecord"`` -> ``"Backup record"``; unknown labels are returned unchanged."""
    model = model_for_label(label)
    if model is None:
        return label
    name = str(model._meta.verbose_name)
    return name[:1].upper() + name[1:]


def _field(model, name):
    if model is None:
        return None
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _label(model, name: str, many: bool = False) -> str:
    if many and name in M2M_LABELS:
        return M2M_LABELS[name]
    field = _field(model, name)
    text = str(getattr(field, "verbose_name", "") or name.replace("_", " "))
    return text[:1].upper() + text[1:]


def _related_names(model, changes: dict) -> dict:
    """``{field name: {id: str(object)}}`` for every relation in ``changes``, one query per field (deleted rows included)."""
    out: dict = {}
    for name, pair in changes.items():
        field = _field(model, name)
        if field is None or not field.is_relation or field.related_model is None or not isinstance(pair, (list, tuple)):
            continue
        if any(isinstance(v, (list, tuple, dict)) for v in pair):
            continue                                    # many-to-many rows already hold names
        ids = {v for v in pair if v not in (None, "", MASK)}
        if not ids:
            continue
        related = field.related_model
        manager = getattr(related, "global_objects", None) or related._base_manager
        try:
            out[name] = {str(pk): str(obj) for pk, obj in manager.in_bulk(list(ids)).items()}
        except (ValueError, TypeError):
            out[name] = {}
    return out


def _value(model, name: str, value, names: dict) -> str:
    if value is None or value == "":
        return EMPTY
    if value == MASK:
        return MASK
    if isinstance(value, (list, tuple)):                    # many-to-many: the related names as stored
        return ", ".join(str(v) for v in value) or EMPTY
    field = _field(model, name)
    if field is None:
        return format_value(value)
    if field.is_relation:
        found = names.get(name, {}).get(str(value))
        return found if found is not None else f"#{value} (deleted)"
    if getattr(field, "choices", None):
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(field, models.BooleanField):
        return "Yes" if value in (True, "True", "true", 1) else "No"
    if isinstance(value, str):
        if isinstance(field, models.DateTimeField):
            value = parse_datetime(value) or value
        elif isinstance(field, models.DateField):
            value = parse_date(value) or value
        elif isinstance(field, models.TimeField):
            value = parse_time(value) or value
    return format_value(value)


def _file_texts(model, name: str, old, new) -> tuple[str, str] | None:
    """(old, new) wording for a file / image field -- only whether it was added, replaced or removed, never the file name."""
    field = _field(model, name)
    if not isinstance(field, models.FileField):
        return None
    kind = "image" if isinstance(field, models.ImageField) else "file"
    if old == new:
        return (kind.capitalize() if old else EMPTY,) * 2
    return (f"Previous {kind}" if old else EMPTY), (f"New {kind}" if new else f"{kind.capitalize()} removed")


def change_rows(log) -> tuple[list, list]:
    """``(new_rows, old_rows)`` as ``[(label, text), ...]``; the object id comes first like a row snapshot."""
    model = model_for_label(log.model_label)
    changes = log.changes if isinstance(log.changes, dict) else {}
    names = _related_names(model, changes)
    new_rows, old_rows = [], []
    if log.object_pk and changes:
        new_rows.append(("Id", log.object_pk))
        old_rows.append(("Id", log.object_pk))
    for name, pair in changes.items():
        old, new = (pair[0], pair[1]) if isinstance(pair, (list, tuple)) and len(pair) == 2 else (None, pair)
        label = _label(model, name, many=isinstance(new, (list, tuple)) or isinstance(old, (list, tuple)))
        old_text, new_text = _file_texts(model, name, old, new) or (_value(model, name, old, names), _value(model, name, new, names))
        new_rows.append((label, new_text))
        old_rows.append((label, old_text))
    return new_rows, old_rows


def snapshot_rows(log) -> list[tuple[str, str, str, bool]]:
    """``[(label, old, new, changed), ...]`` for every field of the snapshot (empty when the row has none)."""
    snap = log.snapshot if isinstance(log.snapshot, dict) else None
    if not snap:
        return []
    model = model_for_label(log.model_label)
    changes = log.changes if isinstance(log.changes, dict) else {}
    pairs = {name: (changes[name][0] if isinstance(changes.get(name), (list, tuple)) and len(changes[name]) == 2 else value, value)
             for name, value in snap.items()}
    names = _related_names(model, {name: list(pair) for name, pair in pairs.items()})
    rows = []
    for name, (old, new) in pairs.items():
        changed = log.event == "update" and name in changes
        old_text, new_text = _file_texts(model, name, old, new) or (_value(model, name, old, names), _value(model, name, new, names))
        rows.append((_label(model, name), old_text, new_text, changed))
    return rows


def _item(label, text, changed):
    if changed:
        return format_html('<li class="audit-changed"><mark>{}: {}</mark></li>', label, text)
    return format_html("<li>{}: {}</li>", label, text)


def _render_lists(sections, note=""):
    """``New:`` / ``Old:`` bullet lists of ``field: value`` (like the reference screens); changed fields are highlighted."""
    def block(title, rows):
        items = format_html_join("", "{}", ((_item(*row),) for row in rows))
        return format_html('<li><span class="fw-medium">{}:</span><ul class="audit-changes-list">{}</ul></li>', title, items)

    return format_html('<ul class="audit-changes mb-0 ps-3">{}</ul>{}', format_html_join("", "{}", ((block(t, r),) for t, r in sections)), note)


def _render_snapshot(log, rows):
    """Every field of the record: an update lists New then Old with the changed fields highlighted in both; a created row has
    only New values, a deleted row only Old values."""
    if log.event == "update":
        note = format_html('<div class="form-hint mt-1">{} of {} fields changed (highlighted).</div>', sum(r[3] for r in rows), len(rows))
        return _render_lists([("New", [(label, new, changed) for label, _old, new, changed in rows]),
                              ("Old", [(label, old, changed) for label, old, _new, changed in rows])], note)
    values = [(label, new, False) for label, _old, new, _c in rows]
    if log.event == "delete":
        return _render_lists([("New", []), ("Old", values)])
    return _render_lists([("New", values), ("Old", [])])


def render_changes(log):
    """Safe HTML: every field with the changed ones highlighted when the row has a snapshot; otherwise the changed fields as a
    "New" then "Old" list (a created row has no "Old"; an event without changes shows a dash)."""
    rows = snapshot_rows(log)
    if rows:
        return _render_snapshot(log, rows)
    new_rows, old_rows = change_rows(log)
    if not new_rows:
        return EMPTY
    changed = log.event == "update"
    sections = [("New", [(label, text, changed and label != "Id") for label, text in new_rows])]
    if log.event != "create":
        sections.append(("Old", [(label, text, changed and label != "Id") for label, text in old_rows]))
    return _render_lists(sections)
