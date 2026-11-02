"""Seeders for core's organisation models (business apps register theirs the same way, see ``registry.py``)."""
from core.models import Company, Department, Designation

from . import values
from .registry import register_seeder

DEPARTMENTS = ["Knitting", "Dyeing", "Finishing", "Merchandising", "Quality", "Store", "Accounts", "HR & Admin", "IT", "Commercial",
               "Planning", "Maintenance"]
DESIGNATIONS = ["Manager", "Assistant Manager", "Executive", "Senior Executive", "Officer", "Supervisor", "Operator", "Merchandiser",
                "QC Inspector", "Coordinator"]


def _pick(names):
    return lambda ctx, n: f"{names[(n - 1) % len(names)]}" + (f" {(n - 1) // len(names) + 1}" if n > len(names) else "")


register_seeder(Company, fields={"name": lambda ctx, n: values.company_name(ctx.rng), "is_parent": False, "parent": None,
                                 "is_active": True, "abbreviation": lambda ctx, n: f"CO{n}"})
register_seeder(Department, fields={"name": _pick(DEPARTMENTS), "parent": None, "is_active": True})
register_seeder(Designation, fields={"name": _pick(DESIGNATIONS), "is_active": True})
