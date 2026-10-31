"""Thin wrapper over django-q2 so the rest of ``core`` never imports it directly (and tests can run tasks inline)."""
from __future__ import annotations

import logging

log = logging.getLogger("core.tasks")


def enqueue(func_path: str, *args, **kwargs) -> str:
    """Queue ``func_path(*args)`` for the django-q2 cluster (``Q_CLUSTER['sync'] = True`` runs it inline, used by tests)."""
    from django_q.tasks import async_task

    return str(async_task(func_path, *args, **kwargs))
