"""Read-only consistency checks that the database itself cannot express."""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from core.models import ApprovalRequest, ApprovalType, ApprovalTypeStep, MenuItem


class Command(BaseCommand):
    help = "Report (never fix) data inconsistencies: approval types without a final step, pending requests without a current step, menu cycles."

    def handle(self, *args, **opts):
        problems = []
        for t in ApprovalType.objects.annotate(f=Count("type_steps", filter=Q(type_steps__is_final=True))).filter(f=0, is_active=True):
            problems.append(f"approval type {t.code} has no final step")
        for r in ApprovalRequest.objects.filter(status="pending"):
            if r.step_at(r.current_sequence) is None:
                problems.append(f"pending request #{r.pk} points at a missing step ({r.current_sequence})")
        from core.menu.service import validate_parent

        for item in MenuItem.objects.exclude(parent=None):
            if not validate_parent(item.pk, item.parent_id):
                problems.append(f"menu item #{item.pk} is part of a cycle")
        for p in problems:
            self.stdout.write(self.style.ERROR(p))
        if problems:
            raise CommandError(f"{len(problems)} problem(s) found")
        self.stdout.write("No problems found.")
