"""Moves the "Audit log" menu entry out of the Administration group to the top level and opens it to superusers AND staff (the
``core.is_staff`` pseudo-permission). Only the default row is touched (matched by its URL name); an entry an administrator already
moved or edited keeps its position."""
from django.db import migrations


def move_row(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    row = MenuItem.objects.filter(url_name="core:audit_log").first()
    if row is None:
        notes = MenuItem.objects.filter(parent=None, url_name="core:notification_list").first()
        MenuItem.objects.create(title="Audit log", icon="history", url_name="core:audit_log", parent=None, permission="core.is_staff",
                                position=(notes.position + 1) if notes and notes.position else 55)
        return
    if row.parent_id is None:
        return
    if row.parent is not None and row.parent.title == "Administration":
        notes = MenuItem.objects.filter(parent=None, url_name="core:notification_list").first()
        row.parent, row.superuser_only, row.permission = None, False, "core.is_staff"
        row.position = (notes.position + 1) if notes and notes.position else 55
        row.save()


def back(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None).first()
    row = MenuItem.objects.filter(url_name="core:audit_log", parent=None).first()
    if admin is not None and row is not None:
        row.parent, row.superuser_only, row.permission = admin, True, ""
        row.save()


class Migration(migrations.Migration):
    dependencies = [("core", "0014_menu_mail")]
    operations = [migrations.RunPython(move_row, back)]
