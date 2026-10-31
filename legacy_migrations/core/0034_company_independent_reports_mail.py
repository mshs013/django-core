"""Saved reports (report builder + query builder) belong to no company any more: they are offered in every company and run on the
active company's rows. "Company" sharing becomes "All users"; a user's reports with the same slug in two companies get a numbered
slug. Mail messages gain ``company``: where the message comes from (shown next to the sender)."""
import django.db.models.deletion
from django.db import migrations, models


def unbind_reports(apps, schema_editor):
    for name in ("ReportDefinition", "QueryReport"):
        Model = apps.get_model("core", name)
        Model.objects.filter(visibility="company").update(visibility="public")
        taken = set()
        for row in Model.objects.order_by("pk").only("pk", "owner_id", "slug"):
            slug, n = row.slug, 1
            while (row.owner_id, slug) in taken:
                n += 1
                slug = f"{row.slug[:160]}-{n}"
            taken.add((row.owner_id, slug))
            if slug != row.slug:
                Model.objects.filter(pk=row.pk).update(slug=slug)


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0033_org_without_company'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='queryreport',
            name='core_queryreport_unique_slug',
        ),
        migrations.RemoveConstraint(
            model_name='reportdefinition',
            name='core_reportdef_unique_slug',
        ),
        migrations.RunPython(unbind_reports, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='queryreport',
            name='company',
        ),
        migrations.RemoveField(
            model_name='reportdefinition',
            name='company',
        ),
        migrations.AddField(
            model_name='mailmessage',
            name='company',
            field=models.ForeignKey(blank=True, help_text="Company the message comes from (the sender's active company, or the record's company for system mail). Mail itself is not company scoped: it shows in every company.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.company'),
        ),
        migrations.AlterField(
            model_name='queryreport',
            name='visibility',
            field=models.CharField(choices=[('private', 'Private (only me)'), ('users', 'Selected users / groups'), ('public', 'All users (with access to the source)')], default='private', max_length=10),
        ),
        migrations.AlterField(
            model_name='reportdefinition',
            name='visibility',
            field=models.CharField(choices=[('private', 'Private (only me)'), ('users', 'Selected users / groups'), ('public', 'All users (with access to the source)')], default='private', max_length=10),
        ),
        migrations.AddConstraint(
            model_name='queryreport',
            constraint=models.UniqueConstraint(fields=('owner', 'slug'), name='core_queryreport_unique_owner_slug'),
        ),
        migrations.AddConstraint(
            model_name='reportdefinition',
            constraint=models.UniqueConstraint(fields=('owner', 'slug'), name='core_reportdef_unique_owner_slug'),
        ),
    ]
