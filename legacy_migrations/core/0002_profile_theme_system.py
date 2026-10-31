from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AlterField(model_name="profile", name="theme",
                              field=models.CharField(choices=[("system", "System"), ("light", "Light"), ("dark", "Dark")], default="system", max_length=6)),
        # existing rows were created with the old default "light" without the user choosing it: move them to the new default
        migrations.RunPython(lambda apps, se: apps.get_model("core", "Profile").objects.filter(theme="light").update(theme="system"),
                             migrations.RunPython.noop),
    ]
