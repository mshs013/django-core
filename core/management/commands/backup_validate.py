from django.core.management.base import BaseCommand, CommandError

from core.backup.service import run_validation
from core.models import BackupRecord


class Command(BaseCommand):
    help = "Validate a stored backup file (checksum + tool-level listing). Never restores anything."

    def add_arguments(self, parser):
        parser.add_argument("record_id", type=int)

    def handle(self, *args, **opts):
        if not BackupRecord.objects.filter(pk=opts["record_id"]).exists():
            raise CommandError("No such backup record.")
        run_validation(opts["record_id"])
        record = BackupRecord.objects.get(pk=opts["record_id"])
        self.stdout.write(f"#{record.pk}: {record.validation_status} {record.validation_message}")
        if record.validation_status != "OK":
            raise CommandError("validation failed")
