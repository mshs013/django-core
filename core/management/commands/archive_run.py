from django.core.management.base import BaseCommand, CommandError

from core.archive import service


class Command(BaseCommand):
    help = "Archive rows older than N days from an allow-listed table (move, verified, FK-safe). Needs --confirm; --preview shows the plan only."

    def add_arguments(self, parser):
        parser.add_argument("table")
        parser.add_argument("--older-than-days", type=int, required=True)
        parser.add_argument("--batch-size", type=int, default=None)
        parser.add_argument("--preview", action="store_true")
        parser.add_argument("--confirm", action="store_true", help="Required to actually move rows.")

    def handle(self, *args, **opts):
        try:
            if opts["preview"] or not opts["confirm"]:
                self.stdout.write(str(service.preview(opts["table"], opts["older_than_days"])))
                if not opts["preview"]:
                    self.stdout.write(self.style.WARNING("Nothing was moved: pass --confirm to run."))
                return
            job = service.create_archive_job(None, opts["table"], opts["older_than_days"], opts["batch_size"], confirm=True)
            job = service.run_archive(job.pk)
        except service.ArchiveError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Job #{job.pk}: {job.status}, moved {job.processed_rows}, blocked {job.blocked_rows}")
        if job.status != "done":
            raise CommandError(job.error or "archive failed")
