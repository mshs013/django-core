"""Adds the "Search builder" entry (superusers) under Administration, after "Saved reports"."""
from django.db import migrations


def add_row(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    admin = MenuItem.objects.filter(title="Administration", parent=None).first()
    if admin is None or MenuItem.objects.filter(url_name="core:search_builder").exists():
        return
    after = MenuItem.objects.filter(parent=admin, url_name="core:report_definitions").first()
    MenuItem.objects.create(title="Search builder", icon="zoom-code", url_name="core:search_builder", parent=admin, superuser_only=True,
                            position=(after.position + 1) if after and after.position else 95)


def remove_row(apps, schema_editor):
    apps.get_model("core", "MenuItem").objects.filter(url_name="core:search_builder").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0022_audit_snapshot")]
    operations = [migrations.RunPython(add_row, remove_row)]
