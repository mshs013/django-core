"""Idempotent first-run setup: migrate-independent data only. Credentials come from the environment, never from source."""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company


class Command(BaseCommand):
    help = ("Create the first company, an optional superuser (DJANGO_SUPERUSER_EMAIL + DJANGO_SUPERUSER_PASSWORD from the environment), "
            "sync approval chains and seed URL permissions. Safe to run repeatedly.")

    def add_arguments(self, parser):
        parser.add_argument("--company-name", default=os.environ.get("BOOTSTRAP_COMPANY_NAME", ""))
        parser.add_argument("--company-abbreviation", default=os.environ.get("BOOTSTRAP_COMPANY_ABBREVIATION", ""))

    @transaction.atomic
    def handle(self, *args, **opts):
        company = Company.objects.order_by("pk").first()
        if company is None:
            name, code = opts["company_name"], opts["company_abbreviation"]
            if not name or not code:
                raise CommandError("No company exists: pass --company-name and --company-abbreviation (or BOOTSTRAP_COMPANY_NAME / _ABBREVIATION).")
            company = Company.objects.create(name=name, abbreviation=code, address="Not set")
            self.stdout.write(f"Created company {company.name}.")
        else:
            self.stdout.write(f"Company exists: {company.name}.")
        email, password = os.environ.get("DJANGO_SUPERUSER_EMAIL", ""), os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        User = get_user_model()
        if email and password:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                user = User.objects.create_superuser(email, password)
                self.stdout.write(f"Created superuser {user.email}.")
            else:
                self.stdout.write(f"Superuser exists: {user.email} (password left untouched).")
            profile = user.profile
            profile.companies.add(company)
            if not profile.active_company_id:
                profile.default_company = profile.active_company = company
                profile.save(update_fields=["default_company", "active_company"])
        else:
            self.stdout.write("No DJANGO_SUPERUSER_EMAIL/DJANGO_SUPERUSER_PASSWORD set: no user created (create one with createsuperuser).")
        call_command("seed_default_menu", stdout=self.stdout)
        call_command("sync_approval_chains", stdout=self.stdout)
        call_command("seed_url_permissions", stdout=self.stdout)
