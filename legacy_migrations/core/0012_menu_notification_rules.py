"""Adds the "Notification rules" entry to the Administration menu group (only that row, only when missing; a deleted or renamed
Administration group is respected)."""
from django.db import migrations


def add_row(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None).first()
    if admin is None or MenuItem.objects.filter(url_name="core:notification_rules").exists():
        return
    after = MenuItem.objects.filter(parent=admin, url_name="core:approval_requests").first()
    MenuItem.objects.create(title="Notification rules", icon="bell-cog", url_name="core:notification_rules", parent=admin,
                            position=(after.position + 1) if after else 999, superuser_only=True)


def remove_row(apps, schema_editor):
    apps.get_model("core", "MenuItem").objects.filter(url_name="core:notification_rules").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0011_notification_rules")]
    operations = [migrations.RunPython(add_row, remove_row)]
