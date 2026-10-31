from django.core.management.base import BaseCommand

from core.backup.service import apply_retention


class Command(BaseCommand):
    help = "Delete backup FILES past retention (history rows are kept; the newest CORE_BACKUP_MIN_KEEP per kind/mode always stay)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        result = apply_retention(dry_run=opts["dry_run"])
        self.stdout.write(f"{'Would delete' if result['dry_run'] else 'Deleted'} {len(result['candidates'])} file(s).")
