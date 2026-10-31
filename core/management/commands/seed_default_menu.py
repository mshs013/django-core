from django.core.management.base import BaseCommand

from core.menu.defaults import seed
from core.menu.service import invalidate_menus
from core.models import MenuItem


class Command(BaseCommand):
    help = "Add any missing default menu rows (Dashboard, Approvals, Reports, Notifications, Administration…). Never edits or deletes rows."

    def handle(self, *args, **opts):
        n = seed(MenuItem)
        invalidate_menus()
        self.stdout.write(f"Created {n} default menu row(s).")
