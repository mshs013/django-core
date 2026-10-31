"""Adds "Roles" and "Permissions" under Administration and opens the Administration group to staff (``core.is_staff``). Every
other default entry in it keeps ``superuser_only``, so staff see only these two (plus anything an administrator opens up)."""
from django.db import migrations

ROWS = (("Roles", "shield-lock", "core:roles"), ("Permissions", "key", "core:permissions"))


def add_rows(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None).first()
    if admin is None:
        return
    if admin.superuser_only and not admin.permission:
        admin.superuser_only, admin.permission = False, "core.is_staff"
        admin.save()
    users = MenuItem.objects.filter(parent=admin, url_name="core:users").first()
    position = (users.position if users and users.position else 40) + 1
    for offset, (title, icon, url_name) in enumerate(ROWS):
        if not MenuItem.objects.filter(url_name=url_name).exists():
            MenuItem.objects.create(title=title, icon=icon, url_name=url_name, parent=admin, permission="core.is_staff",
                                    superuser_only=False, position=position + offset)


def remove_rows(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    MenuItem.objects.filter(url_name__in=[r[2] for r in ROWS]).delete()
    admin = MenuItem.objects.filter(title="Administration", parent=None).first()
    if admin is not None and admin.permission == "core.is_staff":
        admin.superuser_only, admin.permission = True, ""
        admin.save()


class Migration(migrations.Migration):
    dependencies = [("core", "0018_role_proxy")]
    operations = [migrations.RunPython(add_rows, remove_rows)]
