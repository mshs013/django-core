"""Organisation defaults. ``Profile.dept`` / ``designation`` / ``default_company`` are NOT NULL, so a user created outside the user form
(``createsuperuser``, a script, a fixture) gets a profile that points at the first company / department / designation, creating a
clearly named placeholder only when none exist at all. Creating the profile NEVER grants company membership -- access still needs an
explicit membership, so nobody is silently added to a company."""
from __future__ import annotations

from django.db import transaction


def default_org():
    """(company, department, designation) to use as placeholders. Reuses existing rows; creates minimal ones only when absent."""
    from core.models import Company, Department, Designation

    company = Company.objects.filter(is_active=True).order_by("pk").first() or Company.objects.order_by("pk").first()
    if company is None:
        company = Company.objects.create(name="Default Company", abbreviation="DEFAULT", address="Not set")
    dept = Department.objects.order_by("pk").first() or Department.objects.create(name="General")
    designation = Designation.objects.order_by("pk").first() or Designation.objects.create(name="Staff")
    return company, dept, designation


def ensure_profile(user):
    """The user's profile, created with placeholder organisation values if it does not exist yet."""
    from core.models import Profile

    profile = Profile.objects.filter(user=user).first()
    if profile is not None:
        return profile
    with transaction.atomic():
        company, dept, designation = default_org()
        return Profile.objects.create(user=user, default_company=company, dept=dept, designation=designation)
