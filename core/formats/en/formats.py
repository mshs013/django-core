"""English display formats taken from the ``CORE_DATE_FORMAT`` / ``CORE_DATETIME_FORMAT`` / ``CORE_TIME_FORMAT`` settings (read on
access, so they follow the environment). Any format not listed here falls back to Django's own ``en`` locale. For another
``LANGUAGE_CODE`` copy this package to ``core/formats/<language>/`` (or into your own ``FORMAT_MODULE_PATH`` package)."""
from core.formatting import site_formats

_MAP = {"DATE_FORMAT": "date", "SHORT_DATE_FORMAT": "date", "DATETIME_FORMAT": "datetime", "SHORT_DATETIME_FORMAT": "datetime",
        "TIME_FORMAT": "time"}


def __getattr__(name):
    if name in _MAP:
        return site_formats()[_MAP[name]]
    raise AttributeError(name)
