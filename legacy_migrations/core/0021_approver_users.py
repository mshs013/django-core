"""A chain step can have several approver users: ``approver_user`` (one) becomes ``approver_users`` (many). Existing values are
copied over before the old column is dropped. Requests already submitted keep their frozen chain; the approval service reads both
the old ``approver_user_id`` and the new ``approver_user_ids`` keys."""
from django.conf import settings
from django.db import migrations, models


def copy_forward(apps, schema_editor):
    Step = apps.get_model("core", "ApprovalTypeStep")
    for step in Step.objects.exclude(approver_user__isnull=True):
        step.approver_users.add(step.approver_user_id)


def copy_back(apps, schema_editor):
    Step = apps.get_model("core", "ApprovalTypeStep")
    for step in Step.objects.all():
        first = step.approver_users.order_by("pk").first()
        if first is not None:
            step.approver_user = first
            step.save(update_fields=["approver_user"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_row_level_access'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='approvaltypestep',
            name='approver_users',
            field=models.ManyToManyField(blank=True, help_text='People who may decide this step (in addition to the group and the permission).', related_name='approver_steps', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(copy_forward, copy_back),
        migrations.RemoveField(
            model_name='approvaltypestep',
            name='approver_user',
        ),
    ]
