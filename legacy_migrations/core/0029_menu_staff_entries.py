"""Open more Administration entries to staff: the Common group, Departments, Designations, Notification Rules, Backups, Archive
and Export jobs lose their superuser-only flag (staff hold their permissions through ``STAFF_IMPLIED``). Companies, Menu Builder,
Approval Builder, Saved Reports and Search builder stay superuser-only. Rows are matched by URL name (the Common group by title
under Administration); a row an administrator gave another permission keeps it."""
from django.db import migrations

STAFF_ENTRIES = ["core:departments", "core:designations", "core:notification_rules", "core:backup_list", "core:archive_list",
                 "core:export_jobs"]


def forwards(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    MenuItem.objects.filter(url_name__in=STAFF_ENTRIES, superuser_only=True).update(superuser_only=False)
    admin = MenuItem.objects.filter(title="Administration", parent=None, url_name="").first()
    if admin is not None:
        for group in MenuItem.objects.filter(title="Common", parent=admin, url_name="", path=""):
            group.superuser_only, group.permission = False, group.permission or "core.is_staff"
            group.save()


def backwards(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    MenuItem.objects.filter(url_name__in=STAFF_ENTRIES).update(superuser_only=True)
    MenuItem.objects.filter(title="Common", parent__title="Administration", url_name="", path="").update(superuser_only=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0028_approval_signature_export")]
    operations = [migrations.RunPython(forwards, backwards)]
