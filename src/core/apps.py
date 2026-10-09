from django.apps import AppConfig
from django.contrib.admin import apps as admin_apps


class CoreConfig(AppConfig):
    name = "core"
    default = True
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from . import navigation

        navigation.register(
            navigation.NavItem(key="home", label=_("Home"), url_name="home", icon="house", order=10)
        )


class SecureAdminConfig(admin_apps.AdminConfig):
    """django.contrib.admin whose default site is the hidden, MFA-gated SecureAdminSite."""

    default = False
    default_site = "core.admin_site.SecureAdminSite"
