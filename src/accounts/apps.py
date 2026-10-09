from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from core import navigation

        from . import signals  # noqa: F401
        from .policies import can_manage_users

        navigation.register(
            navigation.NavItem(
                key="manage_users",
                label=_("Administration"),
                url_name="manage:user_list",
                icon="people",
                order=90,
                is_visible=can_manage_users,
                active_prefixes=("/manage/",),
            )
        )
