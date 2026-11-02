"""User gets created/updated/deleted stamps (soft delete), and the "Users" menu entry opens to staff (they manage normal users)."""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def forwards(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.update(created_at=models.F("date_joined"))
    apps.get_model("core", "MenuItem").objects.filter(url_name="core:users", superuser_only=True).update(
        superuser_only=False, permission="core.view_user")


def backwards(apps, schema_editor):
    apps.get_model("core", "MenuItem").objects.filter(url_name="core:users", permission="core.view_user").update(
        superuser_only=True, permission="")


def _user_fk(name):
    return models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
                             to=settings.AUTH_USER_MODEL, verbose_name=name)


class Migration(migrations.Migration):
    dependencies = [("core", "0023_menu_search_builder")]
    operations = [
        migrations.AddField("user", "created_at", models.DateTimeField(auto_now_add=True, db_index=True, default=django.utils.timezone.now,
                                                                       verbose_name="created at"), preserve_default=False),
        migrations.AddField("user", "created_by", _user_fk("created by")),
        migrations.AddField("user", "updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
        migrations.AddField("user", "updated_by", _user_fk("updated by")),
        migrations.AddField("user", "deleted_at", models.DateTimeField(blank=True, db_index=True, editable=False, null=True, verbose_name="deleted at")),
        migrations.AddField("user", "deleted_by", _user_fk("deleted by")),
        migrations.RunPython(forwards, backwards),
    ]
