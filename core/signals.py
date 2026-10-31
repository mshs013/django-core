"""Signals emitted by ``core`` (soft delete, approvals) so other apps can hook in without importing internals."""
from django.dispatch import Signal

post_soft_delete = Signal()      # sender=model, instance, user
post_restore = Signal()          # sender=model, instance, user
approval_event = Signal()        # sender=model, instance, request, action, actor
