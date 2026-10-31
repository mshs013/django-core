"""``register_job``: the allow-list of background job handlers. The worker runs only job types registered here (a stored
``job_type`` that is not registered fails instead of running anything), so no request can make it call an arbitrary function.

Register from your app's ``AppConfig.ready()`` (``registrations.py``), like resources and reports::

    from core.jobs import register_job

    @register_job("knitting.recalc_costs", label="Recalculate yarn costs", timeout=1800)
    def recalc(job):                       # job = the JobRecord: job.params, job.set_progress(50, "half way"), job.save_file(...)
        ...
        return {"styles": 120}             # stored as job.result (small, JSON-safe)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_TIMEOUT = 3600


class JobError(Exception):
    """A job problem whose message is safe to show to the user."""


class UnknownJobType(JobError):
    pass


@dataclass(frozen=True)
class JobType:
    name: str
    handler: Callable
    label: str
    timeout: int = DEFAULT_TIMEOUT          # seconds; the django-q2 worker stops the job after this
    allow_retry: bool = True                # a failed run may be started again from the jobs page (never automatic)
    description: str = ""


_REGISTRY: dict[str, JobType] = {}


def register_job(name: str, handler: Callable | None = None, *, label: str = "", timeout: int = DEFAULT_TIMEOUT,
                 allow_retry: bool = True, description: str = "", replace: bool = False):
    """Register ``handler`` as job type ``name``; usable as a decorator (``@register_job("app.name")``) or a plain call."""
    if not name or len(name) > 150:
        raise ValueError("A job type needs a name of at most 150 characters.")

    def add(func: Callable) -> Callable:
        if not callable(func):
            raise TypeError("A job handler must be callable.")
        if name in _REGISTRY and not replace and _REGISTRY[name].handler is not func:
            raise ValueError(f"Job type {name!r} is already registered.")
        _REGISTRY[name] = JobType(name, func, label or name, int(timeout), bool(allow_retry), description)
        return func

    return add(handler) if handler is not None else add


def unregister_job(name: str) -> None:
    _REGISTRY.pop(name, None)


def get_job_type(name: str) -> JobType:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownJobType(f"No handler is registered for job type {name!r}.") from None


def registered_jobs() -> list[JobType]:
    return sorted(_REGISTRY.values(), key=lambda t: t.name)
