from django.core.management.base import BaseCommand

from core.approvals.registry import sync_all_chains


class Command(BaseCommand):
    help = "Create missing approval types/steps from the code-registered chains (get_or_create; never deletes or edits)."

    def handle(self, *args, **opts):
        codes = sync_all_chains()
        self.stdout.write(f"Synced {len(codes)} chain(s): {', '.join(codes) or '-'}")
