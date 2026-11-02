from django.core.management.base import BaseCommand

from core.permissions.seed import seed


class Command(BaseCommand):
    help = "Create the core.access_<url name> permissions for every named URL (never deletes; stale entries are only reported)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be created; write nothing.")

    def handle(self, *args, **opts):
        result = seed(dry_run=opts["dry_run"])
        verb = "Would create" if result["dry_run"] else "Created"
        self.stdout.write(f"{verb} {len(result['created'])} permission(s).")
        for name in result["created"]:
            self.stdout.write(f"  + {name}")
        if result["stale"]:
            self.stdout.write(self.style.WARNING(f"{len(result['stale'])} stale permission(s) kept (never deleted): " + ", ".join(result["stale"])))
        if result["unnamed_patterns"]:
            self.stdout.write(self.style.WARNING(f"{result['unnamed_patterns']} unnamed URL pattern(s): refused for non-superusers (name them to grant access)."))
