from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "audit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
