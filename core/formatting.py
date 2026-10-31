"""Site-wide date / time display format.

The formats come from the environment (``CORE_DATE_FORMAT``, ``CORE_DATETIME_FORMAT``, ``CORE_TIME_FORMAT``; Django ``date`` filter
syntax, e.g. ``d-M-Y h:i:s A`` -> ``21-Oct-2026 06:42:46 AM``) and are installed as Django's own ``DATE_FORMAT`` /
``DATETIME_FORMAT`` / ``TIME_FORMAT`` through ``core.formats`` (``FORMAT_MODULE_PATH``), so ``{{ value }}``, ``{{ value|date }}``,
list/detail pages and previews all use them by default.

Use another format where needed:
* template: ``{{ value|date:"d/m/Y" }}`` (or ``{{ value|fmt:"d/m/Y" }}`` from ``core_tags``, which handles date, datetime and time)
* Python: ``format_value(value, "d/m/Y")`` / ``format_datetime(value, "d/m/Y H:i")``
* one CRUD resource (its list, detail, children and preview): ``register_resource(..., date_formats={"datetime": "d/m/Y H:i"})``
* one custom report (screen + PDF): ``register_custom_report(..., date_formats={...})``

PDF exports follow the same rule: a list page or custom report with its own ``date_formats`` uses them, everything else the
site formats."""
from __future__ import annotations

import datetime as dt
import decimal

from django.utils import dateformat, formats, timezone

from core import conf

KINDS = ("date", "datetime", "time")


def site_formats() -> dict:
    return {"date": conf.get("CORE_DATE_FORMAT"), "datetime": conf.get("CORE_DATETIME_FORMAT"), "time": conf.get("CORE_TIME_FORMAT")}


def _pick(kind: str, fmt: str | dict | None) -> str:
    if isinstance(fmt, dict):
        fmt = fmt.get(kind)
    return fmt or site_formats()[kind]


def format_datetime(value: dt.datetime, fmt: str | dict | None = None) -> str:
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return dateformat.format(value, _pick("datetime", fmt))


def format_date(value: dt.date, fmt: str | dict | None = None) -> str:
    return dateformat.format(value, _pick("date", fmt))


def format_time(value: dt.time, fmt: str | dict | None = None) -> str:
    return dateformat.time_format(value, _pick("time", fmt))


def format_value(value, fmt: str | dict | None = None) -> str:
    """Any value as display text; dates/times in the site format (or ``fmt``: one format string, or ``{"date"|"datetime"|"time": ...}``)."""
    if isinstance(value, dt.datetime):
        return format_datetime(value, fmt)
    if isinstance(value, dt.date):
        return format_date(value, fmt)
    if isinstance(value, dt.time):
        return format_time(value, fmt)
    if isinstance(value, decimal.Decimal):
        return formats.number_format(value)
    return "" if value is None else str(value)
