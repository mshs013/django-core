"""Background jobs for business apps: register a handler once (``register_job``), start runs with ``submit_job``; every run is a
``JobRecord`` listed on the Background jobs page (``/jobs/``). See docs/JOBS.md."""
from .registry import JobError, JobType, UnknownJobType, get_job_type, register_job, registered_jobs, unregister_job  # noqa: F401


def submit_job(*args, **kwargs):
    """See ``core.jobs.service.submit_job`` (imported lazily: this package is imported from ``AppConfig.ready``)."""
    from .service import submit_job as submit

    return submit(*args, **kwargs)


def retry_job(job):
    from .service import retry_job as retry

    return retry(job)
