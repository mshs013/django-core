"""New default menu layout: Administration comes right after Dashboard and holds the sub-groups Common (companies, departments,
designations), Approval Builder (approval types / steps / requests) and Auth (permissions, roles); Audit log moves back inside it, last.

Only default rows are touched (matched by URL name) and only while they are still where the defaults put them (directly under
Administration, or Audit log at the top level); an entry an administrator moved elsewhere stays put. Titles are renamed only while
they still carry the old default title."""
from django.db import migrations

# (url_name or "@group title", children for groups). Groups: (icon, permission, superuser_only).
ADMIN = [
    ("@Common", ["core:companies", "core:departments", "core:designations"]),
    "core:users",
    "core:menu_list",
    ("@Approval Builder", ["core:approval_types", "core:approval_steps", "core:approval_requests"]),
    ("@Auth", ["core:permissions", "core:roles"]),
    "core:notification_rules",
    "core:report_definitions",
    "core:search_builder",
    "core:backup_list",
    "core:archive_list",
    "core:export_jobs",
    "core:audit_log",
]
GROUPS = {"Common": ("building", "", True), "Approval Builder": ("route", "", True), "Auth": ("shield-lock", "core.is_staff", False)}
TOP = ["core:dashboard", "@Administration", "core:approval_queue", "core:report_list", "core:mail_inbox", "core:notification_list"]
RENAMES = {  # url_name: (old default title, new default title)
    "core:menu_list": ("Menu builder", "Menu Builder"),
    "core:approval_types": ("Approval types", "Approval Types"),
    "core:approval_steps": ("Approval steps", "Approval Steps"),
    "core:approval_requests": ("Approval requests", "Approval Requests"),
    "core:notification_rules": ("Notification rules", "Notification Rules"),
    "core:report_definitions": ("Saved reports", "Saved Reports"),
}


def forwards(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None, url_name="").first()
    if admin is None:
        return

    def place(row, parent, position):
        old, new = RENAMES.get(row.url_name, (None, None))
        if old and row.title == old:
            row.title = new
        row.parent, row.position = parent, position
        row.save()

    def movable(row, target):
        return row.parent_id in (admin.pk, target.pk) or (row.url_name == "core:audit_log" and row.parent_id is None)

    for i, entry in enumerate(ADMIN):
        position = (i + 1) * 10
        if isinstance(entry, tuple):
            title = entry[0][1:]
            icon, permission, superuser_only = GROUPS[title]
            group = MenuItem.objects.filter(title=title, parent=admin, url_name="", path="").first()
            if group is None:
                group = MenuItem.objects.create(title=title, icon=icon, permission=permission, superuser_only=superuser_only,
                                                parent=admin, position=position)
            else:
                group.position = position
                group.save()
            for j, url_name in enumerate(entry[1]):
                row = MenuItem.objects.filter(url_name=url_name).first()
                if row is not None and movable(row, group):
                    place(row, group, (j + 1) * 10)
        else:
            row = MenuItem.objects.filter(url_name=entry).first()
            if row is not None and movable(row, admin):
                if entry == "core:audit_log":
                    row.superuser_only, row.permission = False, row.permission or "core.is_staff"
                place(row, admin, position)

    for i, entry in enumerate(TOP):
        row = admin if entry == "@Administration" else MenuItem.objects.filter(url_name=entry, parent=None).first()
        if row is not None:
            row.position = (i + 1) * 10
            row.save()


def backwards(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None, url_name="").first()
    if admin is None:
        return
    for title in GROUPS:
        group = MenuItem.objects.filter(title=title, parent=admin, url_name="").first()
        if group is None:
            continue
        MenuItem.objects.filter(parent=group).update(parent=admin)
        if not MenuItem.objects.filter(parent=group).exists():
            group.delete()
    for url_name, (old, new) in RENAMES.items():
        MenuItem.objects.filter(url_name=url_name, title=new).update(title=old)
    MenuItem.objects.filter(url_name="core:audit_log", parent=admin).update(parent=None, position=55)


class Migration(migrations.Migration):
    dependencies = [("core", "0025_menu_permissions")]
    operations = [migrations.RunPython(forwards, backwards)]
