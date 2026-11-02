"""Export/import formats from django-import-export (tablib): CSV, XLSX, XLS, TSV, ODS, JSON, YAML, HTML -- whichever are installed."""
from __future__ import annotations

from import_export.formats import base_formats

ALL_FORMATS = [base_formats.CSV, base_formats.XLSX, base_formats.XLS, base_formats.TSV, base_formats.ODS, base_formats.JSON,
               base_formats.YAML, base_formats.HTML]


def available_formats() -> dict[str, type]:
    """``{extension: format class}`` for formats whose tablib dependency is importable on this install."""
    out = {}
    for klass in ALL_FORMATS:
        try:
            fmt = klass()
            if fmt.is_available():
                out[fmt.get_extension()] = klass
        except Exception:  # noqa: BLE001 - optional backend missing
            continue
    return out


def get_format(extension: str):
    klass = available_formats().get((extension or "").lower().lstrip("."))
    if klass is None:
        raise ValueError("Unsupported export format.")
    return klass()
