"""Safe value rendering for list/detail pages. Paths come from the registry allowlist only."""
from __future__ import annotations

import datetime as dt
import decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils import formats, timezone

from core.crud.registry import ResourceConfig

EMPTY = "—"


def label_for(model, path: str) -> str:
    current, parts, label = model, path.split("__"), ""
    for part in parts:
        try:
            f = current._meta.get_field(part)
        except FieldDoesNotExist:
            attr = getattr(current, part, None)
            return str(getattr(attr, "short_description", None) or getattr(getattr(attr, "fget", None), "short_description", None)
                       or part.replace("_", " ").capitalize())
        label = str(getattr(f, "verbose_name", part))
        if f.is_relation and f.related_model:
            current = f.related_model
    return label[:1].upper() + label[1:]


def _walk(obj, path: str):
    value = obj
    for part in path.split("__"):
        if value is None:
            return None
        value = getattr(value, part, None)
        if callable(value) and not isinstance(value, models.Manager):
            value = value()
    return value


def display_value(obj, path: str):
    """Return ``(kind, value)`` where kind is text|bool|link|badge|multi|file|binary|json|empty."""
    value = _walk(obj, path)
    field = None
    try:
        field = obj._meta.get_field(path.split("__")[0]) if "__" not in path else None
    except FieldDoesNotExist:
        field = None
    if field is not None:
        if getattr(field, "choices", None):
            return "badge", dict(field.flatchoices).get(value, value) if value not in (None, "") else EMPTY
        if isinstance(field, models.BooleanField):
            return "bool", bool(value)
        if isinstance(field, models.FileField):
            return ("file", value.name) if value else ("empty", EMPTY)
        if isinstance(field, models.BinaryField):
            return "text", f"{len(bytes(value))} bytes" if value else EMPTY
        if isinstance(field, models.JSONField):
            return "json", value
        if field.many_to_many:
            return "multi", [str(x) for x in value.all()[:20]]
    if value is None or value == "":
        return "empty", EMPTY
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, dt.datetime):
        return "text", formats.date_format(timezone.localtime(value) if timezone.is_aware(value) else value, "SHORT_DATETIME_FORMAT")
    if isinstance(value, dt.date):
        return "text", formats.date_format(value, "SHORT_DATE_FORMAT")
    if isinstance(value, decimal.Decimal):
        return "text", formats.number_format(value, use_l10n=True)
    return "text", str(value)


def columns(resource: ResourceConfig):
    return [(path, label_for(resource.model, path)) for path in resource.list_fields]


def rows(resource: ResourceConfig, objects):
    out = []
    for obj in objects:
        out.append({"obj": obj, "cells": [(path, *display_value(obj, path)) for path in resource.list_fields]})
    return out


def detail_items(resource: ResourceConfig, obj):
    items = []
    for name in resource.detail_fields:
        kind, value = display_value(obj, name)
        items.append({"name": name, "label": label_for(resource.model, name), "kind": kind, "value": value})
    return items
