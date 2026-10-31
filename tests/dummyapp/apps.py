from django.apps import AppConfig


class DummyAppConfig(AppConfig):
    """A stand-in *business* app. It depends on core; core never imports it (see tests/test_reusability.py)."""
    name = "tests.dummyapp"
    label = "dummyapp"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import registrations  # noqa: F401
