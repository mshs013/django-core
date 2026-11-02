"""Background jobs page: every run of a ``register_job`` handler. Everyone sees the runs they started; holders of
``core.view_jobrecord`` (staff by default) see everybody's. Retry: own failed runs, or ``core.retry_jobrecord`` for others'."""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views import View

from core.jobs import service
from core.jobs.registry import JobError, registered_jobs
from core.models import JobRecord
from core.permissions import user_has_perm

from .base import CoreMixin


def _sees_all(user) -> bool:
    return user_has_perm(user, "core.view_jobrecord")


def visible_jobs(user):
    qs = JobRecord.objects.select_related("created_by", "company")
    return qs if _sees_all(user) else qs.filter(created_by=user)


def _job_or_404(request, pk: int) -> JobRecord:
    job = visible_jobs(request.user).filter(pk=pk).first()
    if job is None:
        raise Http404
    return job


def _may_retry(user, job: JobRecord) -> bool:
    return service.can_retry(job) and (job.created_by_id == user.pk or user_has_perm(user, "core.retry_jobrecord"))


class JobList(CoreMixin, View):
    def get(self, request):
        qs = visible_jobs(request.user)
        everyone = _sees_all(request.user)
        mine = request.GET.get("mine") == "1" or not everyone
        if mine:
            qs = qs.filter(created_by=request.user)
        job_type, status = request.GET.get("type", ""), request.GET.get("status", "")
        if job_type:
            qs = qs.filter(job_type=job_type)
        if status in dict(JobRecord.STATUSES):
            qs = qs.filter(status=status)
        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        return render(request, "core/jobs/list.html", {
            "page": page, "jobs": page.object_list, "everyone": everyone, "mine": mine, "job_type": job_type, "status": status,
            "types": registered_jobs(), "statuses": JobRecord.STATUSES})


class JobDetail(CoreMixin, View):
    def get(self, request, pk):
        job = _job_or_404(request, pk)
        return render(request, "core/jobs/detail.html", {"job": job, "can_retry": _may_retry(request.user, job)})


class JobRetry(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        job = _job_or_404(request, pk)
        if not _may_retry(request.user, job):
            raise PermissionDenied
        try:
            service.retry_job(job)
            messages.success(request, f"{job} was queued again.")
        except JobError as exc:
            messages.error(request, str(exc))
        return redirect("core:job_detail", pk=job.pk)


class JobDownload(CoreMixin, View):
    def get(self, request, pk):
        from core.files.storage import private_storage

        job = _job_or_404(request, pk)
        if not job.has_file:
            raise Http404("This job has no result file (it may have been removed by retention).")
        try:
            handle = private_storage.open(job.file_name, "rb")
        except (FileNotFoundError, ValueError, OSError):
            raise Http404 from None
        response = FileResponse(handle, as_attachment=True, filename=job.download_name)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response
