"""List the registered background job types and how their runs went (counts per status)."""
from django.core.management.base import BaseCommand
from django.db.models import Count

from core.jobs import registered_jobs
from core.models import JobRecord


class Command(BaseCommand):
    help = "Show every job type registered with core.jobs.register_job and its run counts."

    def handle(self, *args, **opts):
        counts: dict[str, dict[str, int]] = {}
        for row in JobRecord.objects.values("job_type", "status").annotate(n=Count("pk")):
            counts.setdefault(row["job_type"], {})[row["status"]] = row["n"]
        types = registered_jobs()
        if not types:
            self.stdout.write("No job types are registered.")
        for t in types:
            runs = ", ".join(f"{status} {n}" for status, n in sorted(counts.pop(t.name, {}).items())) or "no runs"
            retry = "" if t.allow_retry else ", no retry"
            self.stdout.write(f"{t.name}  {t.label}  (timeout {t.timeout} s{retry})  {runs}")
        for name, by_status in sorted(counts.items()):                  # rows whose type is no longer registered (will not run)
            self.stdout.write(self.style.WARNING(f"{name}  NOT REGISTERED  " + ", ".join(f"{s} {n}" for s, n in sorted(by_status.items()))))
