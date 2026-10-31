"""Environment for the settings package: the ``.env`` reader, typed getters and the choice of settings module.

Standard library only: ``manage.py``, ``wsgi.py`` and ``asgi.py`` import it before Django is set up."""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    """Minimal .env reader (KEY=VALUE, # comments, optional quotes). Real environment variables always win. No extra dependency."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if key:
            os.environ.setdefault(key, value)


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Environment variable {name} is required.")
    return value or ""


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def settings_module(argv: list[str] | None = None) -> str:
    """The settings module when ``DJANGO_SETTINGS_MODULE`` is not set (in the environment or ``.env``): ``config.settings.test``
    for ``manage.py test``, ``config.settings.dev`` when ``DJANGO_DEBUG`` is on, otherwise ``config.settings.production``."""
    load_dotenv()
    argv = sys.argv if argv is None else argv
    if argv[1:2] == ["test"]:
        return "config.settings.test"
    return "config.settings.dev" if env_bool("DJANGO_DEBUG") else "config.settings.production"
