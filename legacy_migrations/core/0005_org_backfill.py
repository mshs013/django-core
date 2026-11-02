"""Step 2 of 3 (data): give existing departments/designations a code and make sure every existing profile has a department,
designation and default company (so step 3 can make them NOT NULL). Only touches rows that lack a value; never overwrites."""
import re

from django.db import migrations


def _code(name: str, taken: set, pk: int) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", (name or "").upper()).strip("-")[:24] or "X"
    code = base
    if code in taken:
        code = f"{base[:20]}-{pk}"
    taken.add(code)
    return code


def backfill(apps, schema_editor):
    Company, Department, Designation, Profile = (apps.get_model("core", n) for n in ("Company", "Department", "Designation", "Profile"))
    for model in (Department, Designation):
        taken_by_company: dict = {}
        for row in model.objects.exclude(code="").filter(deleted_at__isnull=True):
            taken_by_company.setdefault(row.company_id, set()).add(row.code)
        for row in model.objects.filter(code="").order_by("pk"):
            taken = taken_by_company.setdefault(row.company_id, set())
            row.code = _code(row.name, taken, row.pk)
            row.save(update_fields=["code"])

    profiles = Profile.objects.filter(dept__isnull=True) | Profile.objects.filter(designation__isnull=True) | Profile.objects.filter(default_company__isnull=True)
    for profile in profiles.distinct():
        company = (profile.default_company or profile.active_company or profile.companies.filter(deleted_at__isnull=True).order_by("pk").first()
                   or Company.objects.filter(deleted_at__isnull=True).order_by("pk").first())
        if company is None:
            company = Company.objects.create(name="Default Company", abbreviation="DEFAULT", address="Not set")
        if profile.default_company_id is None:
            profile.default_company = company
        if profile.dept_id is None:
            profile.dept = (Department.objects.filter(company=company, deleted_at__isnull=True).order_by("pk").first()
                            or Department.objects.create(company=company, name="General", code="GENERAL"))
        if profile.designation_id is None:
            profile.designation = (Designation.objects.filter(company=company, deleted_at__isnull=True).order_by("pk").first()
                                   or Designation.objects.create(company=company, name="Staff", code="STAFF"))
        profile.save()
        profile.companies.add(profile.default_company_id)


class Migration(migrations.Migration):
    dependencies = [("core", "0004_org_schema")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
