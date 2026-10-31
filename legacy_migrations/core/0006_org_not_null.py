"""Step 3 of 3: department, designation and default company are mandatory on every profile (NOT NULL, PROTECT)."""
import django.db.models.deletion
from django.db import migrations, models

PROTECT = django.db.models.deletion.PROTECT


class Migration(migrations.Migration):
    dependencies = [("core", "0005_org_backfill")]

    operations = [
        migrations.AlterField("profile", "dept", models.ForeignKey(on_delete=PROTECT, related_name="profiles", to="core.department", verbose_name="department")),
        migrations.AlterField("profile", "designation", models.ForeignKey(on_delete=PROTECT, related_name="profiles", to="core.designation")),
        migrations.AlterField("profile", "default_company", models.ForeignKey(on_delete=PROTECT, related_name="default_profiles", to="core.company")),
    ]
