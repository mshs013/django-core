"""Default menu rows now use URL names (core:companies …) instead of literal paths. Converts the rows seeded by 0003 that still carry the
old default path and have no URL name; rows an administrator edited (different path / own URL name) are left alone."""
from django.db import migrations

CONVERT = {"/r/companies/": "core:companies", "/r/departments/": "core:departments", "/r/designations/": "core:designations",
           "/r/users/": "core:users", "/r/approval-types/": "core:approval_types", "/r/approval-steps/": "core:approval_steps",
           "/r/approval-requests/": "core:approval_requests", "/r/report-definitions/": "core:report_definitions", "/r/audit/": "core:audit_log"}


def convert(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    for path, url_name in CONVERT.items():
        MenuItem.objects.filter(path=path, url_name="", superuser_only=True).update(url_name=url_name, path="")


class Migration(migrations.Migration):
    dependencies = [("core", "0006_org_not_null")]
    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]
