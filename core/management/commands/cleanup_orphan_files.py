from django.core.management.base import BaseCommand

from core.files.service import cleanup_orphans


class Command(BaseCommand):
    help = "Remove uploaded files never attached to a record (after the grace period) and unreferenced bytes."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        r = cleanup_orphans(dry_run=opts["dry_run"])
        self.stdout.write(f"{'Would remove' if r['dry_run'] else 'Removed'} {r['rows']} row(s) and {r['files']} stray file(s).")
