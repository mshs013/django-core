"""Adds the top-level "Mail" menu entry (just before Notifications when that row exists; only when missing)."""
from django.db import migrations


def add_row(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    if MenuItem.objects.filter(url_name="core:mail_inbox").exists():
        return
    notes = MenuItem.objects.filter(parent=None, url_name="core:notification_list").first()
    MenuItem.objects.create(title="Mail", icon="mail", url_name="core:mail_inbox", parent=None,
                            position=(notes.position - 1) if notes and notes.position else 35)


def remove_row(apps, schema_editor):
    apps.get_model("core", "MenuItem").objects.filter(url_name="core:mail_inbox").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0013_internal_mail")]
    operations = [migrations.RunPython(add_row, remove_row)]
