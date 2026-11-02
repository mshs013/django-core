"""Menu builder (hierarchy, ordering, permissions). Visibility is presentation; cycle prevention in model + service."""
from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.crud.forms import style_form
from core.menu.service import invalidate_menus, validate_parent
from core.models import MenuItem
from core.permissions import user_has_perm

from .base import CoreMixin
from .modal import is_modal, modal_done


class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = ("title", "icon", "url_name", "path", "parent", "position", "permission", "superuser_only", "is_active")

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        if self.instance.pk:
            self.fields["parent"].queryset = MenuItem.objects.exclude(pk=self.instance.pk)
        style_form(self)

    def clean(self):
        data = super().clean()
        parent = data.get("parent")
        if parent is not None and not validate_parent(self.instance.pk, parent.pk):
            raise ValidationError({"parent": "This would create a cycle in the menu."})
        # no URL name and no path = a group. An empty group is allowed (e.g. "Inventory > Yarn" made before its reports are placed
        # in it on the report's Save form); the sidebar hides it until something visible is under it.
        perm = (data.get("permission") or "").strip()
        data["permission"] = perm
        if perm:
            if not permission_exists(perm):
                self.add_error("permission", f"No permission “{perm}”. Use app_label.codename, e.g. core.view_user.")
        elif (data.get("url_name") or data.get("path")) and not is_dashboard_link(data.get("url_name", ""), data.get("path", "")):
            self.add_error("permission", "Every menu link needs a permission (only the Dashboard is open to everyone).")
        return data


def is_dashboard_link(url_name: str, path: str) -> bool:
    from django.urls import NoReverseMatch, reverse

    try:
        home = reverse("core:dashboard")
    except NoReverseMatch:
        home = "/"
    return url_name == "core:dashboard" or (not url_name and path == home)


def permission_exists(perm: str) -> bool:
    from django.contrib.auth.models import Permission

    from core.permissions import STAFF_PERM

    if perm == STAFF_PERM:
        return True
    app, _, codename = perm.partition(".")
    return bool(app and codename) and Permission.objects.filter(content_type__app_label=app, codename=codename).exists()


class MenuMixin(CoreMixin):
    def need(self, action: str, obj=None):
        if not user_has_perm(self.request.user, f"core.{action}_menuitem", obj):
            raise PermissionDenied


def flatten(parent_id=None, depth=0, items=None):
    items = items if items is not None else list(MenuItem.objects.all())
    out = []
    for it in sorted((i for i in items if i.parent_id == parent_id), key=lambda i: (i.position, i.pk)):
        out.append((it, depth))
        out.extend(flatten(it.pk, depth + 1, items) if depth < 8 else [])
    return out


def tree_rows():
    """The menu as a tree table: each row with its depth, number of children, whether it can move up / down among its siblings,
    and whether it is hidden (inactive itself or under an inactive group)."""
    flat = flatten()
    children, siblings, hidden, rows = {}, {}, set(), []
    for it, _ in flat:
        children[it.parent_id] = children.get(it.parent_id, 0) + 1
        siblings.setdefault(it.parent_id, []).append(it.pk)
    for it, depth in flat:
        off = not it.is_active or it.parent_id in hidden
        if off:
            hidden.add(it.pk)
        group = siblings[it.parent_id]
        rows.append({"item": it, "depth": depth, "children": children.get(it.pk, 0), "first": group[0] == it.pk,
                     "last": group[-1] == it.pk, "off": off, "off_by_parent": off and it.is_active})
    return rows


class MenuList(MenuMixin, View):
    def get(self, request):
        self.need("view")
        return render(request, "core/menu/list.html", {"rows": tree_rows(),
                      "can_change": user_has_perm(request.user, "core.change_menuitem"), "can_add": user_has_perm(request.user, "core.add_menuitem")})


class MenuEdit(MenuMixin, View):
    def _render(self, request, form, instance, status=200):
        if is_modal(request):
            return render(request, "core/menu/_modal_form.html", {"form": form, "obj": instance if instance.pk else None, "action": request.path}, status=status)
        return render(request, "core/menu/form.html", {"form": form, "obj": instance if instance.pk else None}, status=status)

    def get(self, request, pk=None):
        self.need("change" if pk else "add")
        instance = get_object_or_404(MenuItem, pk=pk) if pk else MenuItem()
        return self._render(request, MenuItemForm(instance=instance), instance)

    def post(self, request, pk=None):
        self.need("change" if pk else "add")
        instance = get_object_or_404(MenuItem, pk=pk) if pk else MenuItem()
        form = MenuItemForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            invalidate_menus()
            if is_modal(request):
                return modal_done("Menu saved.")
            messages.success(request, "Menu saved.")
            return redirect("core:menu_list")
        return self._render(request, form, instance, status=400)


class MenuDelete(MenuMixin, View):
    def _confirm(self, request, item):
        context = {"obj": item, "children": item.children.count(), "action": request.path}
        template = "core/menu/_modal_confirm.html" if is_modal(request) else "core/menu/confirm_delete.html"
        return render(request, template, context)

    def get(self, request, pk):
        item = get_object_or_404(MenuItem, pk=pk)
        self.need("delete", item)
        return self._confirm(request, item)

    def post(self, request, pk):
        item = get_object_or_404(MenuItem, pk=pk)
        self.need("delete", item)
        item.delete()                          # children cascade; history is not affected
        invalidate_menus()
        if is_modal(request):
            return modal_done("Menu item deleted.")
        messages.success(request, "Menu item deleted.")
        return redirect("core:menu_list")


class MenuAction(MenuMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        item = get_object_or_404(MenuItem, pk=pk)
        if action == "delete":
            self.need("delete", item)
            item.delete()                      # children cascade; history is not affected
        elif action in ("up", "down"):
            self.need("change", item)
            with transaction.atomic():
                siblings = list(MenuItem.objects.select_for_update().filter(parent=item.parent).order_by("position", "pk"))
                for n, s in enumerate(siblings):                  # normalise, then swap (position is deliberately not unique)
                    s.position = n
                idx = next(i for i, s in enumerate(siblings) if s.pk == item.pk)
                j = idx - 1 if action == "up" else idx + 1
                if 0 <= j < len(siblings):
                    siblings[idx].position, siblings[j].position = siblings[j].position, siblings[idx].position
                for s in siblings:
                    MenuItem.objects.filter(pk=s.pk).update(position=s.position)
        elif action == "toggle":
            self.need("change", item)
            item.is_active = not item.is_active
            item.save(update_fields=["is_active"])
        else:
            raise PermissionDenied
        invalidate_menus()
        return redirect("core:menu_list")
