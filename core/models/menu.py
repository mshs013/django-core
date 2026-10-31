from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from .base import CreatedInfoModel, UpdatedInfoModel


class MenuItem(CreatedInfoModel, UpdatedInfoModel):
    """DB-driven navigation entry. Visibility is a convenience only -- every view still enforces its own permission."""

    title = models.CharField(max_length=80)
    icon = models.CharField(max_length=40, blank=True, help_text="Tabler icon name without the 'ti-' prefix, e.g. 'users'.")
    url_name = models.CharField("URL name", max_length=120, blank=True, help_text="Reversible name such as 'core:dashboard'.")
    path = models.CharField(max_length=200, blank=True, help_text="Internal path starting with '/' (used when no URL name).")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    position = models.PositiveIntegerField(default=0)       # not unique: reordering swaps values
    permission = models.CharField("required permission", max_length=150, blank=True, help_text="app_label.codename; empty = any signed-in user.")
    superuser_only = models.BooleanField(default=False, help_text="Only superusers see this entry (and its children).")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("parent_id", "position", "pk")
        indexes = [models.Index(fields=["parent", "position"], name="core_menuitem_order_idx")]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        super().clean()
        if self.path and not (self.path.startswith("/") and not self.path.startswith("//") and "://" not in self.path):
            raise ValidationError({"path": "Only internal paths starting with a single '/' are allowed."})
        if self.parent_id:
            node, seen = self.parent, set()
            while node is not None:
                if self.pk and node.pk == self.pk:
                    raise ValidationError({"parent": "This would create a cycle in the menu."})
                if node.pk in seen:
                    break
                seen.add(node.pk)
                node = node.parent
            self._check_depth(len(seen))

    def _check_depth(self, ancestors: int):
        from core import conf

        limit = conf.get("CORE_MENU_MAX_DEPTH")
        below, level, seen = 0, [self.pk] if self.pk else [], set()
        while level:                                   # height of this item's own sub-tree (it moves along)
            seen.update(level)
            level = [pk for pk in MenuItem.objects.filter(parent__in=level).values_list("pk", flat=True) if pk not in seen]
            below += bool(level)
        if ancestors + 1 + below > limit:
            raise ValidationError({"parent": f"Menus can be at most {limit} levels deep."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
