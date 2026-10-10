from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "audit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from core import navigation

        from . import signals  # noqa: F401
        from .policies import can_view_audit_log

        navigation.register(
            navigation.NavItem(
                key="audit_log",
                label=_("Audit log"),
                url_name="audit:event_list",
                icon="journal-text",
                order=95,
                is_visible=can_view_audit_log,
                section=navigation.ADMINISTRATION,
                active_prefixes=("/audit/",),
            )
        )
