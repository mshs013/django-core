from django.apps import AppConfig


class SeederConfig(AppConfig):
    """Demo/test data generator. Independent of every business app; remove it from ``INSTALLED_APPS`` in production."""

    name = "core.seeder"
    label = "seeder"
    verbose_name = "Demo data seeder"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from core.menu.service import register_menu_entry

        from . import builtin  # noqa: F401

        register_menu_entry("Seed data", "seeder:index", icon="plant", permission="seeder.use_seeder", parent="Developer tools",
                            position=900)
