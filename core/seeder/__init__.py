"""Demo data seeder: fills every registered model with plausible rows so all features can be tried, and removes them again.

Independent and optional -- nothing in ``core`` imports it. Add ``"core.seeder"`` to ``INSTALLED_APPS`` (dev/staging) and leave it out in
production. Use ``manage.py seed_data`` or *Developer tools › Seed data*. See ``docs/SEEDER.md``.
"""
from .registry import register_seeder, unregister_seeder  # noqa: F401
