from django.db import migrations, models


def seed_menu(apps, schema_editor):
    from core.menu.defaults import seed

    seed(apps.get_model("core", "MenuItem"))


class Migration(migrations.Migration):
    dependencies = [("core", "0002_profile_theme_system")]

    operations = [
        migrations.AddField(model_name="menuitem", name="superuser_only",
                            field=models.BooleanField(default=False, help_text="Only superusers see this entry (and its children).")),
        migrations.RunPython(seed_menu, migrations.RunPython.noop),
    ]
