from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import checks, signals_handlers  # noqa: F401  (registers system checks and signal receivers)
        from .audit import signals as audit_signals  # noqa: F401
        from .bootstrap_registry import register_core_registrations
        from .notifications import rules as notification_rules

        register_core_registrations()
        notification_rules.connect_invalidation()
        post_migrate.connect(signals_handlers.seed_defaults_after_migrate, sender=self, dispatch_uid="core_seed_defaults")
