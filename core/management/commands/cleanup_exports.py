from django.core.management.base import BaseCommand

from core.reports.resources.export import expire_jobs


class Command(BaseCommand):
    help = "Expire finished export jobs past their expiry and delete their files."

    def handle(self, *args, **opts):
        self.stdout.write(f"Expired {expire_jobs()} export(s).")
