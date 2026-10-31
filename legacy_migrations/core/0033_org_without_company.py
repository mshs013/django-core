"""Departments and designations are shared by every company: the ``company`` field goes, names become unique overall.

Rows that now clash by name (e.g. "Accounts" in two companies) are merged into the oldest one: profiles and child departments are
pointed at it and the others are soft deleted (kept for the audit trail, restorable once renamed)."""
from django.db import migrations, models
from django.utils import timezone


def merge_duplicates(apps, schema_editor):
    Profile = apps.get_model("core", "Profile")
    now = timezone.now()
    for model_name, profile_field in (("Department", "dept"), ("Designation", "designation")):
        Model = apps.get_model("core", model_name)
        keepers = {}
        for row in Model.objects.filter(deleted_at__isnull=True).order_by("pk"):
            keeper = keepers.setdefault(row.name, row.pk)
            if keeper == row.pk:
                continue
            Profile.objects.filter(**{f"{profile_field}_id": row.pk}).update(**{f"{profile_field}_id": keeper})
            if model_name == "Department":
                Model.objects.filter(parent_id=row.pk).update(parent_id=keeper)
            Model.objects.filter(pk=row.pk).update(deleted_at=now)
        if model_name == "Department":
            Model.objects.filter(parent_id=models.F("pk")).update(parent=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_record_labels'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='department',
            name='core_department_unique_name_per_company',
        ),
        migrations.RemoveConstraint(
            model_name='designation',
            name='core_designation_unique_name_per_company',
        ),
        migrations.RunPython(merge_duplicates, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='department',
            name='company',
        ),
        migrations.RemoveField(
            model_name='designation',
            name='company',
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='core_department_unique_name'),
        ),
        migrations.AddConstraint(
            model_name='designation',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='core_designation_unique_name'),
        ),
    ]
