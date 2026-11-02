"""Step 1 of 3 (schema, nullable): company hierarchy + contact fields, department/designation code + is_active, profile fields.
Split from the data backfill (0005) and the NOT NULL step (0006) because PostgreSQL refuses to ALTER a table that has pending trigger
events from data changes in the same transaction."""
import django.db.models.deletion
from django.db import migrations, models

import core.files.storage

PRIVATE = core.files.storage.PrivateStorage()
PROTECT = django.db.models.deletion.PROTECT


class Migration(migrations.Migration):
    dependencies = [("core", "0003_menu_defaults")]

    operations = [
        # ---- Company
        migrations.RenameField("company", "code", "abbreviation"),
        migrations.AlterField("company", "abbreviation", models.CharField(max_length=50)),
        migrations.AlterField("company", "name", models.CharField(max_length=255)),
        migrations.AlterField("company", "address", models.TextField()),
        migrations.AddField("company", "is_parent", models.BooleanField(default=False, help_text="Tick for a group company that has child companies.", verbose_name="parent company")),
        migrations.AddField("company", "parent", models.ForeignKey(blank=True, null=True, on_delete=PROTECT, related_name="children", to="core.company", verbose_name="parent company")),
        migrations.AddField("company", "factory_address", models.TextField(blank=True, default="")),
        migrations.AddField("company", "email", models.EmailField(blank=True, default="", max_length=254)),
        migrations.AddField("company", "phone", models.CharField(blank=True, default="", max_length=50)),
        migrations.AddField("company", "fax", models.CharField(blank=True, default="", max_length=50)),
        migrations.AddField("company", "logo", models.ImageField(blank=True, null=True, storage=PRIVATE, upload_to="companies/logos/")),
        migrations.AddField("company", "website", models.URLField(blank=True, default="")),
        migrations.AddConstraint("company", models.CheckConstraint(condition=~(models.Q(is_parent=True) & models.Q(parent__isnull=False)), name="core_company_parent_is_not_parent")),
        migrations.AddConstraint("company", models.CheckConstraint(condition=~models.Q(parent=models.F("pk")), name="core_company_not_own_parent")),
        # ---- Department / Designation
        migrations.AddField("department", "code", models.CharField(default="", max_length=30), preserve_default=False),
        migrations.AddField("department", "is_active", models.BooleanField(default=True)),
        migrations.AddField("designation", "code", models.CharField(default="", max_length=30), preserve_default=False),
        migrations.AddField("designation", "is_active", models.BooleanField(default=True)),
        migrations.AddConstraint("department", models.UniqueConstraint(fields=("company", "code"), condition=models.Q(("deleted_at__isnull", True), models.Q(("code", ""), _negated=True)), name="core_department_unique_code_per_company")),
        migrations.AddConstraint("designation", models.UniqueConstraint(fields=("company", "code"), condition=models.Q(("deleted_at__isnull", True), models.Q(("code", ""), _negated=True)), name="core_designation_unique_code_per_company")),
        # ---- Profile (FKs stay nullable until 0006)
        migrations.RenameField("profile", "phone", "contact_no"),
        migrations.AlterField("profile", "contact_no", models.CharField(blank=True, default="", max_length=50, verbose_name="contact no.")),
        migrations.RenameField("profile", "department", "dept"),
        migrations.AlterField("profile", "dept", models.ForeignKey(null=True, on_delete=PROTECT, related_name="profiles", to="core.department", verbose_name="department")),
        migrations.AlterField("profile", "designation", models.ForeignKey(null=True, on_delete=PROTECT, related_name="profiles", to="core.designation")),
        migrations.AlterField("profile", "default_company", models.ForeignKey(null=True, on_delete=PROTECT, related_name="default_profiles", to="core.company")),
        migrations.AddField("profile", "official_id", models.CharField(blank=True, default="", max_length=50, verbose_name="official ID")),
        migrations.AddField("profile", "joining_date", models.DateField(blank=True, null=True)),
        migrations.AddField("profile", "user_img", models.ImageField(blank=True, null=True, storage=PRIVATE, upload_to="users/img/", verbose_name="photo")),
        migrations.AddField("profile", "user_sign", models.ImageField(blank=True, null=True, storage=PRIVATE, upload_to="users/sign/", verbose_name="signature")),
    ]
