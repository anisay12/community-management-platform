from django.apps import AppConfig


class CommunitiesConfig(AppConfig):
    name = "communities"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from core import navigation

        navigation.register(
            navigation.NavItem(
                key="communities",
                label=_("Communities"),
                url_name="communities:catalogue",
                icon="people",
                order=20,
                active_prefixes=("/communities/",),
            )
        )
