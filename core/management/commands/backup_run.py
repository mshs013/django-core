from django.core.management.base import BaseCommand, CommandError

from core.backup import service
from core.backup.modes import DB_MODES


class Command(BaseCommand):
    help = "Run a database backup now (synchronously, in this process) and print its result."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=DB_MODES, default="FULL")
        parser.add_argument("--alias", default="default")

    def handle(self, *args, **opts):
        try:
            record = service.create_database_backup(None, opts["mode"], opts["alias"])
        except service.BackupError as exc:
            raise CommandError(str(exc)) from exc
        service.run_database_backup(record.pk)
        record.refresh_from_db()
        self.stdout.write(f"#{record.pk} {record.mode}: {record.status} {record.file_name} {record.file_size} bytes sha256={record.sha256[:16]}")
        if record.status != record.SUCCESS:
            raise CommandError(record.error or record.message or "backup failed")
